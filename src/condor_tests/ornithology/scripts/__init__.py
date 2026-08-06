# Copyright 2020 HTCondor Team, Computer Sciences Department,
# University of Wisconsin-Madison, WI.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
import os
from pathlib import Path

SCRIPT_EXT = ".cmd" if sys.platform == "win32" else ".py"

SCRIPTS = {
    path.stem: path.with_suffix(SCRIPT_EXT).as_posix()
    for path in Path(__file__).parent.iterdir()
    if path.stem != "__init__"
}

# A .cmd/.py polyglot.  cmd.exe executes only the single launcher line below;
# it runs python with -x on this same file and then `exit /b` -- so cmd never
# reaches the python body that follows.  python's -x flag ("skip the first line
# of source") exists precisely for this batch/python-polyglot use, so python
# ignores the launcher line and runs the wrapped script normally.
#
# This replaces an earlier multi-line prolog that relied on `goto :EOF` to halt
# cmd before the body.  When cmd instead fell through into the body (its
# documented-here failure mode), it executed the script's `time.sleep(1)` line
# as the cmd built-in `time`, which prompts and blocks reading stdin -- so the
# job never exited (seen in CI as jobs stuck "executing", never "terminated",
# with empty stdout).  A single launcher line ending in `exit /b` makes
# fall-through impossible.
WIN32_PY_LAUNCHER = (
    ('@py.exe' if not sys.executable else '@"{}"'.format(sys.executable))
    + ' -x "%~f0" %* & exit /b'
)

def prepare_script(path):
    if os.path.isfile(path) : return

    source = Path(path).with_suffix(".py")
    if os.path.isfile(source):
        try:
            with open(source, 'rb') as f:
                body = f.read()
            if body:
                # cmd.exe requires CRLF line endings; an LF-only batch file is
                # mis-parsed and the launcher line never runs.  Emit CRLF
                # throughout -- python accepts CRLF source via universal
                # newlines, so the wrapped body still runs.
                prolog = (WIN32_PY_LAUNCHER + "\r\n").encode('utf8')
                body = body.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                with open(path,'wb') as f:
                    f.write(prolog + body)
                os.remove(source)
        except IOError:
            return # do nothing
    return


# Return a space separated list of all custom
# fto plugins to be readily available to all tests
def custom_fto_plugins() -> str:
    if SCRIPT_EXT == ".cmd":
       for name,path in SCRIPTS.items():
          prepare_script(path)

    return " ".join([
        SCRIPTS["null_plugin"],  # null://
        SCRIPTS["debug_plugin"],  # debug://, encode://
    ])
