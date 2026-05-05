"""Python 代码执行 Skill — 在受限环境中执行 Python 代码片段。"""

import asyncio
import io
import sys
import traceback
from typing import Any

from loguru import logger

from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill


@skill(
    name="python_executor",
    description="执行 Python 代码进行计算、数据分析、数学运算。适用于需要精确计算或数据处理的场景",
    category=SkillCategory.COMPUTE,
    icon="🐍",
    config_schema={
        "type": "object",
        "properties": {
            "max_execution_time": {
                "type": "integer",
                "title": "执行超时(秒)",
                "description": "代码执行的最大允许时间",
                "default": 10,
                "minimum": 1,
                "maximum": 60,
            },
            "max_output_length": {
                "type": "integer",
                "title": "最大输出字数",
                "description": "代码输出的最大字符数",
                "default": 10000,
                "minimum": 1000,
                "maximum": 100000,
            },
        },
    },
    parameters_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码",
            },
        },
        "required": ["code"],
    },
)
class PythonExecutorSkill(Skill):
    """在受限环境中执行 Python 代码"""

    # 禁止的模块/函数
    BLOCKED_MODULES = {"subprocess", "shutil", "ctypes", "socket", "webbrowser"}
    BLOCKED_BUILTINS = {"exec", "eval", "compile", "__import__", "open", "input"}

    MAX_EXECUTION_TIME = 10  # 秒
    MAX_OUTPUT_LENGTH = 10000  # 字符

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        code = kwargs.get("code", "")

        if not code.strip():
            return SkillResult.fail("代码不能为空")

        # 安全检查
        safety_error = self._safety_check(code)
        if safety_error:
            return SkillResult.fail(f"安全检查未通过: {safety_error}")

        try:
            output, error = await self._run_code(code)

            if error:
                return SkillResult(
                    success=False,
                    data={"code": code, "error": error},
                    summary=f"代码执行出错:\n```\n{error}\n```",
                    error=error,
                )

            # 截断过长输出
            if len(output) > self.MAX_OUTPUT_LENGTH:
                output = output[:self.MAX_OUTPUT_LENGTH] + "\n...[输出已截断]"

            return SkillResult(
                success=True,
                data={"code": code, "output": output},
                summary=f"代码执行结果:\n```\n{output}\n```" if output else "代码执行完成（无输出）",
                ui_card={
                    "type": "code_result",
                    "code": code,
                    "output": output,
                },
            )

        except asyncio.TimeoutError:
            return SkillResult.fail(f"代码执行超时（>{self.MAX_EXECUTION_TIME}秒）")
        except Exception as e:
            logger.error(f"代码执行异常: {e}", exc_info=True)
            return SkillResult.fail(f"执行异常: {str(e)}")

    def _safety_check(self, code: str) -> str | None:
        """基础安全检查"""
        for module in self.BLOCKED_MODULES:
            if f"import {module}" in code or f"from {module}" in code:
                return f"禁止导入模块: {module}"

        # 检查文件系统操作
        if "open(" in code and ("'w'" in code or '"w"' in code):
            return "禁止写文件操作"

        return None

    async def _run_code(self, code: str) -> tuple[str, str]:
        """在隔离环境中执行代码"""
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, self._execute_in_sandbox, code),
            timeout=self.MAX_EXECUTION_TIME,
        )

    def _execute_in_sandbox(self, code: str) -> tuple[str, str]:
        """沙箱执行"""
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        # 受限的全局环境
        safe_globals = {
            "__builtins__": {
                k: v for k, v in __builtins__.__dict__.items()
                if k not in self.BLOCKED_BUILTINS
            } if isinstance(__builtins__, type(sys)) else {
                k: v for k, v in __builtins__.items()
                if k not in self.BLOCKED_BUILTINS
            },
        }

        # 允许常用计算库
        try:
            import math
            safe_globals["math"] = math
        except ImportError:
            pass

        try:
            import json
            safe_globals["json"] = json
        except ImportError:
            pass

        try:
            import datetime
            safe_globals["datetime"] = datetime
        except ImportError:
            pass

        try:
            import re
            safe_globals["re"] = re
        except ImportError:
            pass

        try:
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture
            exec(code, safe_globals)  # noqa: S102
            return stdout_capture.getvalue(), ""
        except Exception:
            return stdout_capture.getvalue(), traceback.format_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
