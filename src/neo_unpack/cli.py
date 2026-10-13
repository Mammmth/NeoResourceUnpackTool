from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_settings
from .model import FILENAME_ENCODINGS, UnpackError
from .workflow import UnpackTask


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="自动还原与递归解压一个压缩包或一组分卷")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, help="固定输出目录；默认在源目录新建文件夹")
    parser.add_argument("--one-layer", action="store_true")
    parser.add_argument("--keep-intermediates", action="store_true")
    parser.add_argument("--delete-sources", action=argparse.BooleanOptionalAction, default=None,
                        help="成功后删除选中的原文件/全部分卷；默认沿用本机配置，可用 --no-delete-sources 保留")
    parser.add_argument("--password-file", type=Path, help="每行一个密码的 UTF-8 TXT")
    parser.add_argument("--filename-encoding", choices=FILENAME_ENCODINGS,
                        help="旧 ZIP 文件名编码；default 跟随引擎，auto 自动检测，例如 cp932（日语）、gbk（简体中文）")
    args = parser.parse_args()
    task = None
    try:
        settings = load_settings()
        if args.delete_sources is not None:
            settings.delete_sources = args.delete_sources
        if args.filename_encoding:
            settings.filename_encoding = args.filename_encoding
        if args.output:
            settings.output_mode, settings.fixed_directory = "fixed_folder", str(args.output)
        if args.one_layer:
            settings.depth = "one"
        if args.keep_intermediates:
            settings.keep_intermediates = True
        if args.password_file:
            settings.passwords = list(dict.fromkeys(args.password_file.read_text("utf-8-sig").splitlines()))
        task = UnpackTask(args.files, settings, log=print)
        result = task.run()
        print(f"输出：{result.output}")
        return 0
    except (UnpackError, OSError, ValueError) as error:
        print(str(error))
        if task:
            print(f"任务缓存：{task.session}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
