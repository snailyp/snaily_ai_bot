"""
Web 控制面板应用
用于管理机器人配置
"""

import os
import sys

from flask import Flask
from flask_cors import CORS
from loguru import logger

from config.settings import config_manager
from webapp.middleware.auth_middleware import register_auth_middleware
from webapp.routes.ai_api import bp as ai_api_bp
from webapp.routes.auth import bp as auth_bp
from webapp.routes.config_api import bp as config_api_bp
from webapp.routes.errors import bp as errors_bp
from webapp.routes.features_api import bp as features_api_bp
from webapp.routes.main import bp as main_bp
from webapp.routes.status_api import bp as status_api_bp

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def create_app():
    """创建 Flask 应用"""
    app = Flask(__name__)

    # 获取配置
    webapp_config = config_manager.get_webapp_config()
    app.secret_key = webapp_config.get("secret_key", "your-secret-key-here")

    # 启用 CORS
    CORS(app)

    # 注册中间件
    register_auth_middleware(app)

    # 注册蓝图
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(config_api_bp)
    app.register_blueprint(features_api_bp)
    app.register_blueprint(ai_api_bp)
    app.register_blueprint(status_api_bp)
    app.register_blueprint(errors_bp)

    return app


app = create_app()


def run_webapp():
    """运行 Web 应用"""
    try:
        webapp_config = config_manager.get_webapp_config()
        host = webapp_config.get("host", "0.0.0.0")
        port = webapp_config.get("port", 5000)
        debug = webapp_config.get("debug", False)

        logger.info(f"启动 Web 控制面板: http://{host}:{port}")
        app.run(host=host, port=port, debug=debug)

    except Exception as e:
        logger.error(f"启动 Web 应用失败: {e}")
        raise


if __name__ == "__main__":
    run_webapp()
