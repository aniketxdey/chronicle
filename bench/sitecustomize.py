import os

if os.environ.get("CHRONICLE_BENCH_ARM"):
    from chronicle_bench.osworld import install

    install()
