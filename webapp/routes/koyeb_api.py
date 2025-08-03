"""
Koyeb API路由
处理Koyeb服务的重新部署
"""

import requests
from flask import Blueprint, jsonify
from loguru import logger

from config.settings import config_manager

# 创建Koyeb API蓝图
bp = Blueprint("koyeb_api", __name__)


@bp.route("/api/koyeb/redeploy", methods=["POST"])
def redeploy():
    """触发Koyeb服务重新部署 API"""
    try:
        # 从Redis中读取已保存的Koyeb配置
        webapp_config = config_manager.get_webapp_config()
        koyeb_api_token = webapp_config.get("koyeb_api_token")
        koyeb_service_id = webapp_config.get("koyeb_service_id")

        # 检查必需的配置是否存在
        if not koyeb_api_token:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Koyeb API Token 未配置，请先在配置页面设置",
                    }
                ),
                400,
            )

        if not koyeb_service_id:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Koyeb Service ID 未配置，请先在配置页面设置",
                    }
                ),
                400,
            )

        # 构建Koyeb API请求
        koyeb_api_url = f"https://app.koyeb.com/v1/services/{koyeb_service_id}/redeploy"
        headers = {
            "Authorization": f"Bearer {koyeb_api_token}",
            "Content-Type": "application/json",
        }

        logger.info(f"正在向Koyeb API发送重新部署请求: {koyeb_api_url}")

        # 发送POST请求到Koyeb API
        response = requests.post(
            koyeb_api_url,
            headers=headers,
            json={},  # 空的JSON对象作为请求体
            timeout=30,  # 30秒超时
        )

        # 记录响应状态
        logger.info(f"Koyeb API响应状态码: {response.status_code}")

        # 检查响应状态
        if response.status_code == 200:
            logger.info("Koyeb服务重新部署请求成功")
            return jsonify(
                {
                    "success": True,
                    "message": "Koyeb服务重新部署已触发",
                    "koyeb_response": response.json() if response.content else {},
                }
            )
        else:
            # 记录错误响应
            error_msg = f"Koyeb API请求失败，状态码: {response.status_code}"
            if response.content:
                try:
                    error_detail = response.json()
                    error_msg += f", 错误详情: {error_detail}"
                except Exception:
                    error_msg += f", 响应内容: {response.text}"

            logger.error(error_msg)
            return jsonify({"success": False, "error": error_msg}), response.status_code

    except requests.exceptions.Timeout:
        error_msg = "Koyeb API请求超时"
        logger.error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 408

    except requests.exceptions.ConnectionError:
        error_msg = "无法连接到Koyeb API"
        logger.error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 503

    except requests.exceptions.RequestException as e:
        error_msg = f"Koyeb API请求异常: {str(e)}"
        logger.error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 500

    except Exception as e:
        error_msg = f"重新部署时发生未知错误: {str(e)}"
        logger.error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 500
