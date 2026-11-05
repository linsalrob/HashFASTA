#!/usr/bin/env perl
# HashFASTA v0.1 converter (Perl port of the Python reference).
#
# Usage: hashfasta.pl INPUT [-o OUTPUT]   ('-' means stdin / stdout)
#
# Input format is detected from its magic bytes. Core modules handle gzip
# (IO::Uncompress::Gunzip), bzip2 (IO::Uncompress::Bunzip2) and plain text;
# all read concatenated members/streams. xz streams through the system
# `xz -dc` command (xz-utils): CPAN's IO::Uncompress::UnXz rejects valid xz
# stream padding, so it is not used.
# Output uses IO::Compress::Gzip and Digest::SHA.

use strict;
use warnings;

use Digest::SHA qw(sha256_hex);
use IO::Compress::Gzip qw($GzipError);
use IO::Uncompress::Bunzip2 qw($Bunzip2Error);
use IO::Uncompress::Gunzip qw($GunzipError);
use Fcntl qw(O_CREAT O_EXCL O_WRONLY);
use POSIX ();

my $PROG          = 'hashfasta';
my $LINE_WIDTH    = 80;
my @COMPRESS_SFX  = qw(.gz .bgz .bz2 .xz);
my @FASTA_SUFFIX  = qw(.fa .fas .fasta .fna .ffn .faa .frn .fsa);
my $OUTPUT_SUFFIX = '.hash.fasta.gz';
my $USAGE         = "usage: hashfasta INPUT [-o OUTPUT]   ('-' means stdin/stdout)\n";

my $tmp_path;    # removed on error

sub fail {
    my ($msg) = @_;
    print STDERR "$PROG: error: $msg\n";
    unlink $tmp_path if defined $tmp_path;
    exit 1;
}

sub strip_suffix {
    my ($name, @suffixes) = @_;
    for my $suffix (@suffixes) {
        if (length $name >= length $suffix && substr($name, -length $suffix) eq $suffix) {
            return substr($name, 0, length($name) - length $suffix);
        }
    }
    return $name;
}

sub default_output_path {
    my ($name) = @_;
    return strip_suffix(strip_suffix($name, @COMPRESS_SFX), @FASTA_SUFFIX) . $OUTPUT_SUFFIX;
}

sub same_file {
    my ($path_a, $path_b) = @_;
    my @sa = stat $path_a or return 0;
    my @sb = stat $path_b or return 0;
    return $sa[0] == $sb[0] && $sa[1] == $sb[1];
}

sub put {
    my ($out, $data) = @_;
    $out->print($data) or fail("write failed: " . ($GzipError || $!));
}

sub write_record {
    my ($out, $header, $seq, $header_line) = @_;
    fail("record at line $header_line has an empty sequence") unless length $seq;
    (my $escaped = $header) =~ s/([\\\[\]])/\\$1/g;
    put($out, '>sha256_' . sha256_hex($seq) . " [$escaped]\n");
    # Write in blocks of wrapped lines rather than one copy of the record.
    my $block = $LINE_WIDTH * 1024;
    for (my $i = 0; $i < length $seq; $i += $block) {
        put($out, join '', map { "$_\n" } unpack('(a' . $LINE_WIDTH . ')*', substr($seq, $i, $block)));
    }
}

# Stream FASTA records from $in to $out, holding one record in memory.
sub convert {
    my ($in, $out) = @_;
    my ($header, $header_line, $seq, $lineno) = (undef, 0, '', 0);
    while (defined(my $line = $in->{getline}->())) {
        $lineno++;
        if (substr($line, 0, 1) eq '>') {
            write_record($out, $header, $seq, $header_line) if defined $header;
            $header = substr($line, 1);
            $header =~ tr/\r//d;
            $header =~ s/\n\z//;
            $header_line = $lineno;
            $seq = '';
            next;
        }
        next if substr($line, 0, 1) eq ';';    # comment line (spec section 3)
        $line =~ tr/ \t\n\x0b\x0c\r//d;
        next unless length $line;
        fail("line $lineno: sequence data before the first '>' header") unless defined $header;
        if (index($line, ';') >= 0) {
            fail("line $lineno: invalid sequence byte ';' (allowed only at the start of a "
                    . "comment line) in record starting at line $header_line");
        }
        if ($line =~ /([^\x21-\x7e])/) {
            fail(sprintf("line %d: invalid sequence byte 0x%02x (non-ASCII or non-printable) "
                    . "in record starting at line %d", $lineno, ord $1, $header_line));
        }
        $line =~ tr/a-z/A-Z/;
        $seq .= $line;
    }
    $in->{finish}->();
    fail("input contains no FASTA records") unless defined $header;
    write_record($out, $header, $seq, $header_line);
}

# Reader over an IO::Uncompress object; $errref points at its package error.
sub module_reader {
    my ($z, $errref) = @_;
    return {
        getline => sub { $z->getline },
        finish  => sub {
            my $err = $z->error || $$errref;
            fail("cannot read input: $err") if $err;
        },
    };
}

# xz: a child process feeds the already-read leading bytes and the rest of
# the input to `xz -dc`, whose output we read.
sub xz_pipe_reader {
    my ($fh, $head) = @_;
    my $pid = open(my $pipe, '-|');
    fail("cannot fork for xz: $!") unless defined $pid;
    if ($pid == 0) {
        open(my $xz, '|-', 'xz', '-dc') or POSIX::_exit(127);
        binmode $xz;
        print {$xz} $head;
        my $buf;
        while (read($fh, $buf, 1 << 16)) {
            print {$xz} $buf or last;
        }
        close $xz;
        POSIX::_exit($? ? 1 : 0);
    }
    binmode $pipe;
    return {
        getline => sub { scalar <$pipe> },
        finish  => sub {
            close $pipe
                or fail("xz decompression failed (corrupt input, or no xz command)");
        },
    };
}

sub open_input {
    my ($path) = @_;
    my $fh;
    if ($path eq '-') {
        $fh = \*STDIN;
    } else {
        open $fh, '<', $path or fail("cannot open input $path: $!");
    }
    binmode $fh;
    my $head = '';
    my $n = read($fh, $head, 6);
    fail("cannot read input $path: $!") unless defined $n;

    fail("zstd-compressed input is not supported; decompress it first")
        if substr($head, 0, 4) eq "\x28\xb5\x2f\xfd";
    fail("ZIP-compressed input is not supported; decompress it first")
        if substr($head, 0, 4) eq "PK\x03\x04";

    if (substr($head, 0, 3) eq 'BZh') {
        my $z = IO::Uncompress::Bunzip2->new($fh, Prime => $head, MultiStream => 1, Transparent => 0)
            or fail("cannot read input $path: $Bunzip2Error");
        return module_reader($z, \$Bunzip2Error);
    }
    if ($head eq "\xfd7zXZ\x00") {
        return xz_pipe_reader($fh, $head);
    }
    # gzip, or plain text passed through (Transparent) when there is no gzip
    # magic. Transparent must be off for gzip, or junk after the last member
    # would be passed through as if it were plain text.
    my $is_gzip = substr($head, 0, 2) eq "\x1f\x8b";
    my $z = IO::Uncompress::Gunzip->new($fh, Prime => $head, MultiStream => 1,
                                        Transparent => $is_gzip ? 0 : 1, Strict => 1)
        or fail("cannot read input $path: $GunzipError");
    return module_reader($z, \$GunzipError);
}

# Exclusively create a temporary sibling of $output, recorded in $tmp_path.
# O_EXCL refuses any existing name, including a symlink or hard link to the
# input, so a planted temporary path can never redirect the write.
sub create_temp {
    my ($output) = @_;
    for my $n (0 .. 999) {
        my $path = "$output.tmp.$$.$n";
        if (sysopen my $fh, $path, O_WRONLY | O_CREAT | O_EXCL, 0666) {
            $tmp_path = $path;
            return $fh;
        }
        fail("cannot create $output: $!") unless $!{EEXIST};
    }
    fail("cannot create a temporary file beside $output");
}

sub open_output {
    my ($fh) = @_;
    binmode $fh;
    my $out = IO::Compress::Gzip->new($fh, Time => 0, Minimal => 1)
        or fail("cannot start gzip output: $GzipError");
    return $out;
}

sub main {
    my ($input, $output);
    while (@ARGV) {
        my $arg = shift @ARGV;
        if ($arg eq '-h' || $arg eq '--help') {
            print $USAGE;
            exit 0;
        } elsif ($arg eq '-o' && @ARGV) {
            $output = shift @ARGV;
        } elsif (($arg ne '-' && $arg =~ /^-/) || defined $input) {
            print STDERR $USAGE;
            exit 2;
        } else {
            $input = $arg;
        }
    }
    unless (defined $input) {
        print STDERR $USAGE;
        exit 2;
    }
    $output = $input eq '-' ? '-' : default_output_path($input) unless defined $output;
    if ($input ne '-' && $output ne '-' && same_file($input, $output)) {
        fail("output path $output would overwrite the input");
    }

    my $in = open_input($input);
    if ($output eq '-') {
        my $out = open_output(\*STDOUT);
        convert($in, $out);
        $out->close or fail("closing output failed: $GzipError");
        return;
    }

    # Write a temporary sibling, renamed into place only on success.
    my $fh = create_temp($output);
    my $out = open_output($fh);
    convert($in, $out);
    $out->close or fail("closing output failed: $GzipError");
    close $fh or fail("closing output failed: $!");
    rename $tmp_path, $output or fail("cannot write $output: $!");
    undef $tmp_path;
}

main();
exit 0;
