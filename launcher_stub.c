/* Tiny native stub for Fund Research Tool.app/Contents/MacOS/launcher.
 *
 * A plain shell script here works fine when run directly or via `open`
 * on some setups, but modern macOS refuses to launch a shebang script as
 * a GUI app's CFBundleExecutable (exits immediately, status 126) even
 * when the bundle is ad-hoc code-signed. A real Mach-O binary is not
 * subject to that restriction, so this just execs the actual launcher
 * logic in start.command — no logic is duplicated here.
 *
 * Rebuild after editing (from the project root):
 *   cc -arch arm64 -arch x86_64 -o "Fund Research Tool.app/Contents/MacOS/launcher" launcher_stub.c
 */
#include <string.h>
#include <libgen.h>
#include <unistd.h>
#include <stdio.h>

int main(int argc, char *argv[]) {
    char buf[4096];
    strncpy(buf, argv[0], sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    char path[4096];
    snprintf(path, sizeof(path), "%s/../../../start.command", dirname(buf));

    execl(path, path, (char *)NULL);
    return 1; /* only reached if execl itself failed to launch */
}
