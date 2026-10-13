import sys

if "--smoke-test" in sys.argv:
    from neo_unpack.selftest import main
    main(sys.argv[sys.argv.index("--smoke-test") + 1])
else:
    from neo_unpack.gui import main
    main()
