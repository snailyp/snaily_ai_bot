#!/usr/bin/env python3
"""
部署脚本
用于检查环境和配置，确保机器人可以正常运行
"""

import os
import subprocess
import sys
from pathlib import Path


def check_python_version():
    """检查 Python 版本"""
    print("🐍 检查 Python 版本...")
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 10):
        print(f"❌ Python 版本过低: {version.major}.{version.minor}")
        print("   需要 Python 3.10 或更高版本")
        return False
    print(f"✅ Python 版本: {version.major}.{version.minor}.{version.micro}")
    return True


def check_dependencies():
    """检查依赖包"""
    print("\n📦 检查依赖包...")

    if not os.path.exists("requirements.txt"):
        print("❌ requirements.txt 文件不存在")
        return False

    try:
        # 尝试导入关键依赖
        import flask
        import loguru
        import openai
        import telegram

        print("✅ 关键依赖包已安装")
        return True
    except ImportError as e:
        print(f"❌ 缺少依赖包: {e}")
        print("   请运行: pip install -r requirements.txt")
        return False


def check_config():
    """检查环境配置文件"""
    print("\n⚙️ 检查环境配置...")

    env_path = ".env"
    example_path = ".env.example"

    if not os.path.exists(example_path):
        print("❌ 环境配置示例文件不存在")
        return False

    if not os.path.exists(env_path):
        print("⚠️ 环境配置文件 .env 不存在")
        print("   请手动创建 .env 文件并配置必要的环境变量")
        print(f"   可以参考项目中的 {example_path} 文件来设置环境变量")
        print("   必要的环境变量包括:")
        print("   - TELEGRAM_BOT_TOKEN: Telegram Bot Token")
        print("   - OPENAI_API_KEY: OpenAI API Key")
        return False

    # 检查环境变量内容
    try:
        # 读取 .env 文件内容
        env_vars = {}
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()

        # 检查必要配置
        bot_token = env_vars.get("TELEGRAM_BOT_TOKEN", "")
        openai_key = env_vars.get("OPENAI_API_KEY", "")

        if not bot_token or bot_token == "YOUR_BOT_TOKEN_HERE":
            print("⚠️ Telegram Bot Token 未设置")
            print("   请在 .env 文件中设置有效的 TELEGRAM_BOT_TOKEN")
        else:
            print("✅ Telegram Bot Token 已设置")

        if not openai_key or openai_key == "YOUR_OPENAI_API_KEY_HERE":
            print("⚠️ OpenAI API Key 未设置")
            print("   AI 功能将不可用，请在 .env 文件中设置 OPENAI_API_KEY")
        else:
            print("✅ OpenAI API Key 已设置")

        return True

    except Exception as e:
        print(f"❌ 读取环境配置文件失败: {e}")
        return False


def create_directories():
    """创建必要的目录"""
    print("\n📁 创建必要目录...")

    directories = ["logs", "data"]

    for directory in directories:
        try:
            os.makedirs(directory, exist_ok=True)
            print(f"✅ 目录已创建: {directory}/")
        except Exception as e:
            print(f"❌ 创建目录失败 {directory}: {e}")
            return False

    return True


def check_permissions():
    """检查文件权限"""
    print("\n🔐 检查文件权限...")

    # 检查关键文件的读写权限
    files_to_check = [".env", "logs", "data"]

    for file_path in files_to_check:
        if os.path.exists(file_path):
            if os.access(file_path, os.R_OK | os.W_OK):
                print(f"✅ {file_path} 权限正常")
            else:
                print(f"❌ {file_path} 权限不足")
                return False

    return True


def run_basic_test():
    """运行基础测试"""
    print("\n🧪 运行基础测试...")

    try:
        # 测试配置管理器
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        from config.settings import config_manager

        # 测试配置加载
        config_manager.load_config()
        print("✅ 配置管理器测试通过")

        # 测试功能检查
        chat_enabled = config_manager.is_feature_enabled("chat")
        print(f"✅ 功能检查测试通过 (chat: {chat_enabled})")

        return True

    except Exception as e:
        print(f"❌ 基础测试失败: {e}")
        return False


def main():
    """主函数"""
    print("🐌 小蜗AI助手部署检查")
    print("=" * 50)

    checks = [
        ("Python 版本", check_python_version),
        ("依赖包", check_dependencies),
        ("配置文件", check_config),
        ("目录结构", create_directories),
        ("文件权限", check_permissions),
        ("基础测试", run_basic_test),
    ]

    all_passed = True

    for name, check_func in checks:
        if not check_func():
            all_passed = False

    print("\n" + "=" * 50)

    if all_passed:
        print("🎉 所有检查通过！机器人已准备就绪")
        print("\n📋 下一步操作:")
        print("1. 创建并编辑 .env 文件，设置必要的环境变量")
        print("   可以参考 .env.example 文件来配置")
        print("2. 运行: python run_bot.py")
        print("3. 访问 Web 控制面板: http://localhost:5000")
    else:
        print("❌ 部分检查未通过，请解决上述问题后重新运行")
        sys.exit(1)


if __name__ == "__main__":
    main()
