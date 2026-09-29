import sys
from candor.actions import run
run(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "data")
