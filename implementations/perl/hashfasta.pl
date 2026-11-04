#!/usr/bin/env perl
# HashFASTA v0.1 converter (Perl port of the Python reference).
#
# Usage: hashfasta.pl INPUT [-o OUTPUT]   ('-' means stdin / stdout)
#
# Core modules only: IO::Uncompress::Gunzip (detects gzip by magic bytes,
# passes plain input through, reads concatenated members), IO::Compress::Gzip
# and Digest::SHA.

use strict;
use warnings;

use Digest::SHA qw(sha256_hex);
use IO::Compress::Gzip qw($GzipError);
use IO::Uncompress::Gunzip qw($GunzipError);

my $PROG          = 'hashfasta';
my $LINE_WIDTH    = 80;
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

sub default_output_path {
    my ($name) = @_;
    $name =~ s/\.gz\z//;
    for my $suffix (@FASTA_SUFFIX) {
        if (length $name >= length $suffix && substr($name, -length $suffix) eq $suffix) {
            substr($name, -length $suffix) = '';
            last;
        }
    }
    return $name . $OUTPUT_SUFFIX;
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
    my $text = '>sha256_' . sha256_hex($seq) . " [$escaped]\n";
    for (my $i = 0; $i < length $seq; $i += $LINE_WIDTH) {
        $text .= substr($seq, $i, $LINE_WIDTH) . "\n";
    }
    put($out, $text);
}

# Stream FASTA records from $in to $out, holding one record in memory.
sub convert {
    my ($in, $out) = @_;
    my ($header, $header_line, $seq, $lineno) = (undef, 0, '', 0);
    while (defined(my $line = $in->getline)) {
        $lineno++;
        if (substr($line, 0, 1) eq '>') {
            write_record($out, $header, $seq, $header_line) if defined $header;
            $header = substr($line, 1);
            $header =~ s/\n\z//;
            $header =~ s/\r\z//;
            $header_line = $lineno;
            $seq = '';
            next;
        }
        $line =~ tr/ \t\n\x0b\x0c\r//d;
        next unless length $line;
        fail("line $lineno: sequence data before the first '>' header") unless defined $header;
        if ($line =~ /([^\x21-\x7e])/) {
            fail(sprintf("line %d: invalid sequence byte 0x%02x (non-ASCII or non-printable) "
                    . "in record starting at line %d", $lineno, ord $1, $header_line));
        }
        $line =~ tr/a-z/A-Z/;
        $seq .= $line;
    }
    fail("read failed: $GunzipError") if $in->error || $GunzipError;
    fail("input contains no FASTA records") unless defined $header;
    write_record($out, $header, $seq, $header_line);
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
    my $in = IO::Uncompress::Gunzip->new($fh, MultiStream => 1, Transparent => 1, Strict => 1)
        or fail("cannot read input $path: $GunzipError");
    return $in;
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
    $tmp_path = "$output.tmp.$$";
    open my $fh, '>', $tmp_path or do {
        my $err = $!;
        undef $tmp_path;
        fail("cannot create $output: $err");
    };
    my $out = open_output($fh);
    convert($in, $out);
    $out->close or fail("closing output failed: $GzipError");
    close $fh or fail("closing output failed: $!");
    rename $tmp_path, $output or fail("cannot write $output: $!");
    undef $tmp_path;
}

main();
exit 0;
