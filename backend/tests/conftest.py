"""测试装配：隔离数据目录 + 可导入 app 包。

单元测试完全离线（不碰真机、不调模型）；标 @pytest.mark.live 的用例
需要正在运行的后端 + CNetNexus 容器，CI 里默认跳过，可用 -m live 单跑。
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="detops-test-")
os.environ["DETOPS_DATA_DIR"] = _TMP

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pytest  # noqa: E402

from app.core.db import init_db  # noqa: E402
from app.modules.kb import models as _kb_models          # noqa: E402,F401
from app.modules.devices import models as _dev_models    # noqa: E402,F401
from app.modules.collect import models as _col_models    # noqa: E402,F401
from app.modules.diagnose import models as _diag_models  # noqa: E402,F401
from app.modules.events import models as _evt_models     # noqa: E402,F401
from app.modules.mibs import models as _mib_models       # noqa: E402,F401
from app.modules.settings import models as _set_models   # noqa: E402,F401

init_db()


def pytest_collection_modifyitems(config, items):
    if config.getoption("-m", default=""):
        return
    skip = pytest.mark.skip(reason="live 用例需真机环境，用 -m live 运行")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
