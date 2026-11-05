/* runstat: run a command and report its wall time, CPU time and peak RSS.
 *
 *   runstat COMMAND [ARGS...]
 *
 * Prints "wall_s user_s sys_s max_rss_kib" to stderr's last line and exits
 * with the command's status. Linux charges a child with the resident size of
 * the process that forked it, so measuring from a large interpreter (e.g.
 * Python's subprocess) puts a floor of that interpreter's RSS under every
 * result; this launcher is small, so the floor is about 1 MiB. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static double seconds(struct timeval tv) { return tv.tv_sec + tv.tv_usec / 1e6; }

int main(int argc, char **argv) {
    struct timespec start, end;
    struct rusage usage;
    int status;
    pid_t pid;

    if (argc < 2) {
        fprintf(stderr, "usage: runstat COMMAND [ARGS...]\n");
        return 2;
    }
    clock_gettime(CLOCK_MONOTONIC, &start);
    pid = fork();
    if (pid < 0) {
        perror("fork");
        return 2;
    }
    if (pid == 0) {
        execvp(argv[1], argv + 1);
        perror(argv[1]);
        _exit(127);
    }
    if (wait4(pid, &status, 0, &usage) < 0) {
        perror("wait4");
        return 2;
    }
    clock_gettime(CLOCK_MONOTONIC, &end);
    fprintf(stderr, "%.6f %.6f %.6f %ld\n",
            (end.tv_sec - start.tv_sec) + (end.tv_nsec - start.tv_nsec) / 1e9,
            seconds(usage.ru_utime), seconds(usage.ru_stime), usage.ru_maxrss);
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
