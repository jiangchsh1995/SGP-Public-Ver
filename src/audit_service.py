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
from pathlib import Path
from typing import Dict, Any
from datetime import datetime

from .watermark_service import check_watermark, _read_image_cv2


def generate_audit_report(file_path: str, config: Dict[str, Any]) -> str:
    """
    生成水印审计报告
    
    Args:
        file_path: 图像文件路径
        config: 配置字典
    
    Returns:
        报告文件路径
    """
    print(f"\n[审计报告] 正在生成报告: {file_path}")
    
    # 检查水印
    result = check_watermark(file_path, config)
    
    # 生成报告内容
    report_lines = [
        "=" * 60,
        "SGP 水印审计报告 (ShadowGuard Protocol Audit Report)",
        "=" * 60,
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"文件路径: {result['file_path']}",
        f"图像尺寸: {result['image_size'][0]} x {result['image_size'][1]}",
        "-" * 60,
    ]
    
    if result['has_watermark']:
        report_lines.extend([
            "水印状态: ✅ 检测成功",
            f"置信度: {result['confidence']*100:.2f}%",
            "",
            "水印信息:",
            f"  原始作者 UID: {result['original_uid']}",
            f"  当前持有者 UID: {result['current_uid']}",
        ])
        
        if result['is_master']:
            report_lines.append("  版本类型: 🎯 MASTER COPY (母带版本)")
        else:
            report_lines.append("  版本类型: 📦 DISTRIBUTION COPY (分发版本)")
        
        report_lines.extend([
            "",
            "权限配置:",
            f"  允许转载: {'✅ 是' if result['allow_reprint'] else '❌ 否'}",
            f"  允许二创: {'✅ 是' if result['allow_derivative'] else '❌ 否'}",
        ])
    else:
        report_lines.extend([
            "水印状态: ❌ 未检测到水印",
            f"置信度: {result['confidence']*100:.2f}%",
            "",
            "说明: 该图像可能未经 SGP 系统处理，或水印已被破坏。"
        ])
    
    report_lines.append("=" * 60)
    
    report_text = "\n".join(report_lines)
    
    # 保存报告
    report_dir = Path("output_reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    
    filename = Path(file_path).stem
    report_path = report_dir / f"Report_{filename}.txt"
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    print(f"[审计报告] ✅ 报告已保存: {report_path}")
    
    # 同时打印到控制台
    print("\n" + report_text)
    
    return str(report_path)


def batch_audit(directory: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    批量审计目录下的所有图像
    
    Args:
        directory: 目标目录
        config: 配置字典
    
    Returns:
        批量审计统计结果
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        raise ValueError(f"目录不存在: {directory}")
    
    print(f"\n[批量审计] 正在扫描目录: {directory}")
    
    # 支持的图像格式
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp'}
    image_files = [f for f in dir_path.iterdir() 
                   if f.is_file() and f.suffix.lower() in image_extensions]
    
    if not image_files:
        print("[批量审计] 未找到图像文件")
        return {'total': 0, 'with_watermark': 0, 'without_watermark': 0}
    
    print(f"[批量审计] 找到 {len(image_files)} 个图像文件")
    
    stats = {
        'total': len(image_files),
        'with_watermark': 0,
        'without_watermark': 0,
        'master_copies': 0,
        'distribution_copies': 0,
        'results': []
    }
    
    for img_file in image_files:
        try:
            result = check_watermark(str(img_file), config)
            stats['results'].append(result)
            
            if result['has_watermark']:
                stats['with_watermark'] += 1
                if result['is_master']:
                    stats['master_copies'] += 1
                else:
                    stats['distribution_copies'] += 1
            else:
                stats['without_watermark'] += 1
        except Exception as e:
            print(f"[批量审计] ⚠️ 处理失败 {img_file.name}: {e}")
    
    # 打印统计摘要
    print("\n" + "=" * 60)
    print("批量审计统计摘要")
    print("=" * 60)
    print(f"总文件数: {stats['total']}")
    print(f"含水印: {stats['with_watermark']} ({stats['with_watermark']/stats['total']*100:.1f}%)")
    print(f"  - 母带版本: {stats['master_copies']}")
    print(f"  - 分发版本: {stats['distribution_copies']}")
    print(f"无水印: {stats['without_watermark']} ({stats['without_watermark']/stats['total']*100:.1f}%)")
    print("=" * 60)
    
    return stats
