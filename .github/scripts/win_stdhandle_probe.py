#!/usr/bin/env python3
"""
Temporary CI diagnostic: run job-like children with stdout/stderr handles that
match what the condor_starter hands a job, and report whether their output
survives.

Since HTCONDOR-3975 (task packing), the starter on Windows opens job stdout and
stderr with CreateFile(FILE_APPEND_DATA) and nothing else.  Before that, the
C runtime's _open() gave the job GENERIC_WRITE.  On GitHub Actions the job
output comes back empty; on batlab it does not.  This probe tells us which
programs lose output with which handles.

Usage:  python win_stdhandle_probe.py [path-to-src/condor_tests]
"""

import ctypes
import ctypes.wintypes as wt
import msvcrt
import os
import shutil
import subprocess
import sys
import tempfile
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_APPEND_DATA = 0x00000004
SHARE_ALL = 0x7  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, as the starter uses
CREATE_ALWAYS = 2
OPEN_EXISTING = 3
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class SECURITY_ATTRIBUTES(ctypes.Structure):
	_fields_ = [("nLength", wt.DWORD), ("lpSecurityDescriptor", wt.LPVOID), ("bInheritHandle", wt.BOOL)]


class OBJECT_BASIC_INFORMATION(ctypes.Structure):
	_fields_ = [("Attributes", wt.ULONG), ("GrantedAccess", wt.ULONG), ("HandleCount", wt.ULONG),
	            ("PointerCount", wt.ULONG), ("Reserved", wt.ULONG * 10)]


k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(SECURITY_ATTRIBUTES),
                            wt.DWORD, wt.DWORD, wt.HANDLE]
ntdll.NtQueryObject.restype = ctypes.c_long
ntdll.NtQueryObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.ULONG, ctypes.POINTER(wt.ULONG)]

ACCESS_NAMES = [
	(0x00000001, "READ_DATA"), (0x00000002, "WRITE_DATA"), (0x00000004, "APPEND_DATA"),
	(0x00000008, "READ_EA"), (0x00000010, "WRITE_EA"), (0x00000080, "READ_ATTRIBUTES"),
	(0x00000100, "WRITE_ATTRIBUTES"), (0x00020000, "READ_CONTROL"), (0x00100000, "SYNCHRONIZE"),
]


def open_handle(path, access, disposition):
	"""Open the way UserProc::openStdFile does on WIN32: share all, inheritable."""
	sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
	h = k32.CreateFileW(path, access, SHARE_ALL, ctypes.byref(sa), disposition, FILE_ATTRIBUTE_NORMAL, None)
	if h is None or h == INVALID_HANDLE_VALUE:
		raise ctypes.WinError(ctypes.get_last_error())
	return h


def granted_access(h):
	info = OBJECT_BASIC_INFORMATION()
	needed = wt.ULONG()
	status = ntdll.NtQueryObject(h, 0, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(needed))
	if status != 0:
		return "NtQueryObject failed 0x%08x" % (status & 0xFFFFFFFF)
	mask = info.GrantedAccess
	names = [n for bit, n in ACCESS_NAMES if mask & bit]
	return "0x%08x %s" % (mask, "|".join(names))


def find_perl():
	"""The perl the starter would run: the one the .pl file association points at."""
	r = subprocess.run("ftype PerlScript", shell=True, capture_output=True, text=True)
	line = r.stdout.strip()
	print("ftype PerlScript ->", line or "(none)", r.stderr.strip())
	if '"' in line:
		path = line.split('"')[1]
		if os.path.exists(path):
			return path
	return shutil.which("perl")


ACCESS_MODES = {"GENERIC_WRITE": GENERIC_WRITE, "APPEND_ONLY": FILE_APPEND_DATA}

# (label, stdout access, stderr target, stderr access)
HANDLE_CONFIGS = [
	("A pre-3975:  out=GENERIC_WRITE  err=NUL GENERIC_WRITE", "GENERIC_WRITE", "NUL", "GENERIC_WRITE"),
	("B main now:  out=APPEND_ONLY    err=NUL APPEND_ONLY  ", "APPEND_ONLY", "NUL", "APPEND_ONLY"),
	("C main now:  out=APPEND_ONLY    err=file APPEND_ONLY ", "APPEND_ONLY", "file", "APPEND_ONLY"),
	("D isolate:   out=GENERIC_WRITE  err=NUL APPEND_ONLY  ", "GENERIC_WRITE", "NUL", "APPEND_ONLY"),
	("E isolate:   out=APPEND_ONLY    err=NUL GENERIC_WRITE", "APPEND_ONLY", "NUL", "GENERIC_WRITE"),
]

PERL_DIAG = r"""
my $log = shift @ARGV;
open(my $L, '>>', $log) or die "cannot open $log: $!";
my @st = stat(STDOUT);
print $L "perl $] $^X\n";
print $L "  -t STDOUT=" . (-t STDOUT ? 1 : 0) . " stat=" . (@st ? "ok mode=$st[2] size=$st[7]" : "FAIL($!)")
       . " tell=" . tell(STDOUT) . " fileno=" . fileno(STDOUT) . "\n";
$! = 0; my $p = print STDOUT "hello from perl diag: @ARGV\n";
print $L "  print STDOUT=" . ($p ? 1 : 0) . " errno=" . ($! + 0) . " ($!)\n";
$! = 0; my $c = close(STDOUT);
print $L "  close STDOUT=" . ($c ? 1 : 0) . " errno=" . ($! + 0) . " ($!)\n";
$! = 0; my $e = print STDERR "hello from perl diag on stderr\n";
print $L "  print STDERR=" . ($e ? 1 : 0) . " errno=" . ($! + 0) . " ($!)\n";
close($L);
exit 0;
"""


def run_one(cfg, name, argv, workdir, diag_log):
	label, out_mode, err_target, err_mode = cfg
	outdir = tempfile.mkdtemp(prefix="probe_", dir=workdir)
	out_path = os.path.join(outdir, "_condor_stdout")
	err_path = "NUL" if err_target == "NUL" else os.path.join(outdir, "_condor_stderr")

	# Same create dispositions as the starter: stdin NUL read, stdout truncate,
	# NUL without truncate (O_TRUNC is cleared for NUL).
	h_in = open_handle("NUL", GENERIC_READ, OPEN_EXISTING)
	h_out = open_handle(out_path, ACCESS_MODES[out_mode], CREATE_ALWAYS)
	h_err = open_handle(err_path, ACCESS_MODES[err_mode], OPEN_ALWAYS if err_target == "NUL" else CREATE_ALWAYS)
	fds = [msvcrt.open_osfhandle(h, 0) for h in (h_in, h_out, h_err)]

	argv = [a.replace("@DIAGLOG@", diag_log) for a in argv]
	start = time.monotonic()
	try:
		# Popen duplicates these with DUPLICATE_SAME_ACCESS, so the child gets the same rights.
		p = subprocess.Popen(argv, stdin=fds[0], stdout=fds[1], stderr=fds[2], cwd=outdir)
		rc = p.wait(timeout=120)
	except subprocess.TimeoutExpired:
		p.kill()
		rc = "TIMEOUT"
	except OSError as e:
		rc = "LAUNCH FAILED: %s" % e
	elapsed = time.monotonic() - start
	for fd in fds:
		os.close(fd)

	with open(out_path, "rb") as f:
		out = f.read()
	line = "    %-28s rc=%-4s %5.1fs  stdout=%4dB %r" % (name, rc, elapsed, len(out), out[:80])
	if err_target != "NUL":
		with open(err_path, "rb") as f:
			err = f.read()
		line += "  stderr=%dB %r" % (len(err), err[:80])
	print(line, flush=True)
	return len(out) > 0


def main():
	tests_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join("src", "condor_tests")
	workdir = tempfile.mkdtemp(prefix="stdhandle_probe_")

	print("=== environment")
	subprocess.run("where perl", shell=True)
	subprocess.run("assoc .pl", shell=True)
	perl = find_perl()
	print("perl used for probe:", perl)
	subprocess.run([perl, "-e", "print qq(perl $] on $^O, $^X\\n)"])
	subprocess.run([perl, "-V:cc", "-V:ccversion", "-V:gccversion", "-V:libpth"])
	print("python:", sys.version)
	sys.stdout.flush()

	diag_pl = os.path.join(workdir, "diag.pl")
	with open(diag_pl, "w") as f:
		f.write(PERL_DIAG)

	commands = [
		("x_print-args.pl Chicken", [perl, os.path.abspath(os.path.join(tests_dir, "x_print-args.pl")), "Chicken"]),
		("x_getsandbox.pl 1", [perl, os.path.abspath(os.path.join(tests_dir, "x_getsandbox.pl")), "1"]),
		("perl diag", [perl, diag_pl, "@DIAGLOG@", "Chicken"]),
		("perl -e system(cmd echo)", [perl, "-e", "system(q(cmd /c echo hello from cmd via perl)); exit($? >> 8)"]),
		("cmd /c echo", ["cmd", "/c", "echo hello from cmd"]),
		("python print", [sys.executable, "-c", "print('hello from python')"]),
	]

	summary = []
	for cfg in HANDLE_CONFIGS:
		print("\n=== config", cfg[0])
		# Show what the kernel actually granted for this combination.
		probe_dir = tempfile.mkdtemp(prefix="access_", dir=workdir)
		for what, path, mode, disp in (
				("stdout", os.path.join(probe_dir, "o"), cfg[1], CREATE_ALWAYS),
				("stderr", "NUL" if cfg[2] == "NUL" else os.path.join(probe_dir, "e"), cfg[3],
				 OPEN_ALWAYS if cfg[2] == "NUL" else CREATE_ALWAYS)):
			h = open_handle(path, ACCESS_MODES[mode], disp)
			print("    granted %s: %s" % (what, granted_access(h)))
			k32.CloseHandle(h)

		diag_log = os.path.join(workdir, "diag_%s.txt" % cfg[0][0])
		for name, argv in commands:
			ok = run_one(cfg, name, argv, workdir, diag_log)
			summary.append((cfg[0][0], name, ok))
		if os.path.exists(diag_log):
			with open(diag_log) as f:
				print("    perl diag log:\n" + "".join("      " + l for l in f))

	print("\n=== summary (stdout non-empty?)")
	names = [n for n, _ in commands]
	print("    %-28s %s" % ("", "  ".join(c[0][0] for c in HANDLE_CONFIGS)))
	for n in names:
		row = ["Y" if ok else "-" for (c, nm, ok) in summary if nm == n]
		print("    %-28s %s" % (n, "  ".join(row)))
	return 0


if __name__ == "__main__":
	sys.exit(main())
