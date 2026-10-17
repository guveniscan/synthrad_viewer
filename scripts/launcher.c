/* Native main executable of "SynthRAD Viewer.app" (built by install_mac_app.sh).
 *
 * Runs the copy of Python's GUI interpreter that sits next to it, so macOS shows the app's
 * own name and icon, with the project's venv selected via __PYVENV_LAUNCHER__ (the
 * variable Python's own launcher sets). If a Python upgrade removed the framework that
 * copy links against, it falls back to the venv's python.
 *
 * VENV_PYTHON and FRAMEWORK_DYLIB are passed with -D at build time.
 */
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    (void)argc;
    char self[PATH_MAX], dir[PATH_MAX], bundled[PATH_MAX];
    uint32_t size = sizeof(self);
    if (_NSGetExecutablePath(self, &size) != 0) return 1;
    strlcpy(dir, dirname(self), sizeof(dir));
    snprintf(bundled, sizeof(bundled), "%s/SynthRAD Viewer", dir);

    char *args[] = {NULL, "-m", "synthrad_viewer", NULL};
    (void)argv;
    if (access(bundled, X_OK) == 0 && access(FRAMEWORK_DYLIB, R_OK) == 0) {
        setenv("__PYVENV_LAUNCHER__", VENV_PYTHON, 1);
        args[0] = bundled;
        execv(bundled, args);
    }
    unsetenv("__PYVENV_LAUNCHER__");
    args[0] = VENV_PYTHON;
    execv(VENV_PYTHON, args);
    perror("SynthRAD Viewer: cannot start Python");
    return 1;
}
