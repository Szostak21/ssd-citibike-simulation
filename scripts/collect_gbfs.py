import sys

from pipeline.gbfs_collector import run

if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
