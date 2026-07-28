"""Compatibility entrypoint for the canonical fundamental-first builder.

The scheduled Docker pipeline executes the implementation under
``stock_ai_v21``. Keeping a second copy here previously allowed the manual and
scheduled paths to drift, so this legacy path now delegates to the canonical
module.
"""

from stock_ai_v21.fundamental_first_system.build_fundamental_first import main


if __name__ == '__main__':
    raise SystemExit(main())
