"""示例脚本的冒烟测试。

**示例是项目最好的文档**——但它们此前**从不被执行**（CI 只跑
``examples/reliability_report.py``）。结果就是：一个已经下线的模型 id 在示例的
docstring 里躺了很久没人发现。

这个测试把每个示例都跑一遍：**示例坏了，CI 就红** ✓。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.py"))

TIMEOUT = 180
"""单个示例的超时（秒）。"""


def test_examples_exist() -> None:
    """示例目录不能空——否则这个测试是假的。"""
    assert len(EXAMPLES) >= 20


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda path: path.stem)
def test_example_runs(script: Path) -> None:
    """每个示例都要能跑通。

    Args:
        script: 示例脚本。
    """
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 0, (
        f"{script.name} 退出码 {result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-2000:]}\n"
        f"--- stderr ---\n{result.stderr[-2000:]}"
    )
