# -------------------------------------------------------------------------
# PROPRIETARY SOURCE CODE / 专有源代码
# Copyright (c) 2025 JCHSH. All Rights Reserved.
#
# This code is provided for SECURITY AUDIT PURPOSES ONLY to authorized
# server administrators.
# Copying, modifying, distributing, or reverse engineering this code
# without written permission is strictly prohibited.
# -------------------------------------------------------------------------

import cv2
import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from pathlib import Path
import configparser
import uuid
from typing import Dict, Any

from .watermark_core import (
    embed_watermark,
    extract_watermark,
    WatermarkPayload,
    NULL_UID
)


def load_config(config_path: str = 'config.ini') -> Dict[str, Any]:
    """
    加载配置文件（热重载）
    
    Args:
        config_path: 配置文件路径
    
    Returns:
        配置字典
    """
    config = configparser.ConfigParser()
    config.read(config_path, encoding='utf-8-sig')
    
    return {
        'bot_token': config.get('Discord', 'bot_token', fallback=''),
        'workers': config.getint('System', 'workers', fallback=4),
        'auto_cleanup': config.getboolean('System', 'auto_cleanup', fallback=True),
        'watermark_key': config.get('Security', 'watermark_key'),
        'master_dir': config.get('Paths', 'master_dir', fallback='storage/masters'),
        'dist_dir': config.get('Paths', 'dist_dir', fallback='storage/distribution'),
        'input_dir': config.get('Paths', 'input_dir', fallback='input_images'),
        'owner_uuid': int(config.get('Identity', 'owner_uuid')),
        'allow_reprint': config.getboolean('Permissions', 'allow_reprint', fallback=False),
        'allow_derivative': config.getboolean('Permissions', 'allow_derivative', fallback=False),
        'qim_step': config.getfloat('Algorithm', 'qim_step', fallback=40.0)
    }


def _read_image_cv2(file_path: str) -> np.ndarray:
    """
    使用 cv2.imdecode 读取图像（支持中文路径）
    
    Args:
        file_path: 图像文件路径
    
    Returns:
        BGR 图像数组
    """
    with open(file_path, 'rb') as f:
        file_data = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(file_data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"无法读取图像: {file_path}")
    return img


def _save_image_with_metadata(img: np.ndarray, output_path: str, original_path: str = None):
    """
    保存图像并保留元数据（使用 PIL，兼容酒馆）
    
    Args:
        img: BGR 图像数组
        output_path: 输出路径
        original_path: 原始图像路径（用于复制元数据）
    """
    # BGR -> RGB
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img_rgb)
    
    # 复制元数据
    metadata = PngInfo()
    if original_path:
        try:
            original_pil = Image.open(original_path)
            if hasattr(original_pil, 'text'):
                for key, value in original_pil.text.items():
                    metadata.add_text(key, value)
        except Exception:
            pass
    
    # 保存
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    pil_img.save(output_path, 'PNG', pnginfo=metadata, optimize=False)


def create_master_copy(file_path: str, config: Dict[str, Any]) -> str:
    """
    创建母带版本（作者通道）
    
    Args:
        file_path: 输入图像路径
        config: 配置字典
    
    Returns:
        母带文件路径
    
    Raises:
        PermissionError: DRM 鉴权失败
    """
    print(f"\n[母带制作] 正在处理: {file_path}")
    
    # 读取图像
    img = _read_image_cv2(file_path)
    h, w = img.shape[:2]
    print(f"[母带制作] 图像尺寸: {w}x{h}")
    
    # 提取现有水印（DRM 鉴权）
    existing_payload, confidence = extract_watermark(
        img,
        config['watermark_key'],
        config['qim_step']
    )
    
    owner_uuid = config['owner_uuid']
    allow_reprint = config['allow_reprint']
    allow_derivative = config['allow_derivative']
    
    # DRM 四种场景鉴权
    if existing_payload is None:
        # Case A: 无水印，新建母带
        print("[母带制作] 场景 A: 检测到无水印图像，创建新母带")
        payload = WatermarkPayload(
            original_uid=owner_uuid,
            current_uid=NULL_UID,
            allow_reprint=allow_reprint,
            allow_derivative=allow_derivative
        )
    elif existing_payload.original_uid == owner_uuid:
        # Case B: 原作者，更新母带
        print(f"[母带制作] 场景 B: 原作者更新母带 (置信度: {confidence*100:.1f}%)")
        payload = WatermarkPayload(
            original_uid=owner_uuid,
            current_uid=NULL_UID,
            allow_reprint=allow_reprint,
            allow_derivative=allow_derivative
        )
    elif existing_payload.allow_derivative:
        # Case C: 他人 + 允许二创，Fork 分叉
        print(f"[母带制作] 场景 C: 分叉与重新创作 (原作者: {existing_payload.original_uid})")
        payload = WatermarkPayload(
            original_uid=owner_uuid,  # 更新原作者
            current_uid=NULL_UID,
            allow_reprint=allow_reprint,
            allow_derivative=allow_derivative
        )
    else:
        # Case D: 他人 + 禁止二创，拒绝
        raise PermissionError(
            f"DRM 鉴权失败: 原作者 {existing_payload.original_uid} 禁止二次创作"
        )
    
    # 嵌入水印
    img_watermarked = embed_watermark(
        img,
        payload,
        config['watermark_key'],
        config['qim_step']
    )
    
    # 生成输出路径
    master_dir = Path(config['master_dir'])
    master_dir.mkdir(parents=True, exist_ok=True)
    
    filename = Path(file_path).stem
    ext = Path(file_path).suffix
    output_path = master_dir / f"{filename}_master{ext}"
    
    # 保存（保留元数据）
    _save_image_with_metadata(img_watermarked, str(output_path), file_path)
    
    print(f"[母带制作] ✅ 母带已保存: {output_path}")
    return str(output_path)


def generate_distribution(master_path: str, user_uuid: int, config: Dict[str, Any]) -> str:
    """
    生成分发版本（用户通道）
    
    Args:
        master_path: 母带文件完整路径（绝对路径）
        user_uuid: 用户 UID (25 位)
        config: 配置字典
    
    Returns:
        分发文件路径
    
    Raises:
        ValueError: 母带不存在或水印读取失败
    """
    # 直接使用传入的完整路径
    master_path = Path(master_path)
    
    if not master_path.exists():
        raise ValueError(f"母带文件不存在: {master_path}")
    
    print(f"\n[分发生成] 正在为用户 {user_uuid} 生成分发版本")
    print(f"[分发生成] 母带文件: {master_path}")
    
    # 读取母带
    img = _read_image_cv2(str(master_path))
    
    # 提取母带水印
    payload, confidence = extract_watermark(
        img,
        config['watermark_key'],
        config['qim_step']
    )
    
    if payload is None:
        raise ValueError(f"无法从母带中提取水印 (置信度: {confidence*100:.1f}%)")
    
    if not payload.is_master():
        raise ValueError(f"该文件不是母带 (Current UID: {payload.current_uid})")
    
    print(f"[分发生成] 母带验证通过 (原作者: {payload.original_uid}, 置信度: {confidence*100:.1f}%)")
    
    # 铸造分发版本 Payload（保持 Original，更新 Current）
    dist_payload = WatermarkPayload(
        original_uid=payload.original_uid,  # 保持不变
        current_uid=user_uuid,              # 更新为用户 UID
        allow_reprint=payload.allow_reprint,
        allow_derivative=payload.allow_derivative
    )
    
    # 嵌入分发水印
    img_dist = embed_watermark(
        img,
        dist_payload,
        config['watermark_key'],
        config['qim_step']
    )
    
    # 生成唯一文件名（UUID4 防冲突）
    dist_dir = Path(config['dist_dir'])
    dist_dir.mkdir(parents=True, exist_ok=True)
    
    unique_id = uuid.uuid4().hex[:8]
    ext = master_path.suffix
    output_path = dist_dir / f"{user_uuid}_{unique_id}{ext}"
    
    # 保存（保留元数据）
    _save_image_with_metadata(img_dist, str(output_path), str(master_path))
    
    print(f"[分发生成] ✅ 分发版本已保存: {output_path}")
    return str(output_path)


def update_master_permissions(master_path: str, allow_reprint: bool, allow_derivative: bool, config: Dict[str, Any]) -> bool:
    """
    更新母带文件的权限水印
    
    Args:
        master_path: 母带文件完整路径
        allow_reprint: 是否允许转载
        allow_derivative: 是否允许二次创作
        config: 配置字典
    
    Returns:
        是否更新成功
    """
    master_path = Path(master_path)
    
    if not master_path.exists():
        raise ValueError(f"母带文件不存在: {master_path}")
    
    print(f"\n[权限更新] 正在更新母带权限: {master_path}")
    
    # 读取母带
    img = _read_image_cv2(str(master_path))
    
    # 提取现有水印
    payload, confidence = extract_watermark(
        img,
        config['watermark_key'],
        config['qim_step']
    )
    
    if payload is None:
        raise ValueError(f"无法从母带中提取水印 (置信度: {confidence*100:.1f}%)")
    
    if not payload.is_master():
        raise ValueError(f"该文件不是母带 (Current UID: {payload.current_uid})")
    
    print(f"[权限更新] 当前权限 - 转载: {payload.allow_reprint}, 二改: {payload.allow_derivative}")
    print(f"[权限更新] 新权限 - 转载: {allow_reprint}, 二改: {allow_derivative}")
    
    # 创建新的 Payload（保持 UID，更新权限）
    new_payload = WatermarkPayload(
        original_uid=payload.original_uid,  # 保持不变
        current_uid=NULL_UID,                # 保持母带状态
        allow_reprint=allow_reprint,         # 更新
        allow_derivative=allow_derivative    # 更新
    )
    
    # 重新嵌入水印
    img_updated = embed_watermark(
        img,
        new_payload,
        config['watermark_key'],
        config['qim_step']
    )
    
    # 覆盖保存（保留元数据）
    _save_image_with_metadata(img_updated, str(master_path), str(master_path))
    
    print(f"[权限更新] ✅ 母带权限已更新")
    return True


def check_watermark(file_path: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    检查图像水印信息
    
    Args:
        file_path: 图像文件路径
        config: 配置字典
    
    Returns:
        检查结果字典
    """
    print(f"\n[水印检查] 正在扫描: {file_path}")
    
    img = _read_image_cv2(file_path)
    h, w = img.shape[:2]
    
    payload, confidence = extract_watermark(
        img,
        config['watermark_key'],
        config['qim_step']
    )
    
    result = {
        'file_path': file_path,
        'image_size': (w, h),
        'has_watermark': payload is not None,
        'confidence': confidence
    }
    
    if payload:
        result.update({
            'original_uid': payload.original_uid,
            'current_uid': payload.current_uid,
            'is_master': payload.is_master(),
            'allow_reprint': payload.allow_reprint,
            'allow_derivative': payload.allow_derivative
        })
        
        if payload.is_master():
            print(f"[水印检查] ✅ 检测到母带版本 (原作者: {payload.original_uid})")
        else:
            print(f"[水印检查] ✅ 检测到分发版本 (原作者: {payload.original_uid}, 当前持有: {payload.current_uid})")
        print(f"[水印检查] 置信度: {confidence*100:.1f}%")
    else:
        print(f"[水印检查] ❌ 未检测到水印")
    
    return result
