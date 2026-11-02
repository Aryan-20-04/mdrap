import traceback, sys, io, contextlib, tempfile, os, warnings, logging, time
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
import mdrap; from mdrap import cli as C
hits = 0; other = 0; N = 12
for attempt in range(N):
    d = tempfile.mkdtemp(); db = os.path.join(d, "t.db"); buf = io.StringIO()
    sys.argv = ["mdrap", "simulate", "--scale", "custom", "--normal", "1", "--fast", "1", "-t", "0.5", "--db", db]
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf): C.main()
    except SystemExit: pass
    except ZeroDivisionError: hits += 1
    except Exception: other += 1
print(mdrap.__file__.split("/src")[0].split("/")[-1], f": ZeroDivisionError in {hits}/{N}, other errors {other}")
