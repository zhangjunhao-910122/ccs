# -*- coding: utf-8 -*-
"""生成 tools/fixtures/ 下的 9001 体系文件样本（供导入器与锚点表演示/测试）。

用法（仓库根目录执行）：
    /i/python3.10/python.exe tools/fixtures/build_fixtures.py

样本分三类：
  1. 合格材料：quality_manual_A1.md、process_site_register.xlsx、
     internal_audit_report_2026Q2.docx
  2. 带矛盾条款的版本：procedure_traceability_A0.md
  3. 不可读材料：supplier_cert_scan.pdf（无文本层扫描件）、garbled_scan.txt（乱码）

全部内容为虚构示例公司材料，只使用标准库与已安装的第三方库，不联网。
"""
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 1. 合格体系手册（Markdown）
# ---------------------------------------------------------------------------
QUALITY_MANUAL = """# 示例公司质量管理体系手册

版本：A/1.0

生效日期：2026-03-01

编制部门：质量管理部

## 过程的质量管理体系

4.4 公司应建立、实施、保持和持续改进过程管理体系，以获得预期结果并增强实现质量目标的能力。

## 生产和服务提供的控制

8.1 公司应策划、实施和控制满足产品和服务提供要求所需的过程，并按策划的时间间隔进行评审。

8.5.2 公司应实施策划的控制，以保持产品和服务符合要求，并保留形成可追溯性的信息。

7.5.3 保存的文件应予以控制，以确保在需要时可获得并适合其用途。

10.2 公司应按策划的时间间隔进行内部审核，以提供质量管理体系符合性的信息。

## 过程与资源

生产过程：投料到成品入库的工序流转由《装配作业指导书》定义。

一号车间：建筑面积 1200 平方米，划分为投料区、装配区与检验区。

检测仪器：三坐标测量机，用于首件尺寸检验。

批次追溯台账：登记批次号、物料批号与检验结论，保存期三年。

## 监视测量分析评价

9.1 公司应确定需要监视测量的对象、方法、时机，并评价过程绩效与产品符合性。
"""

# ---------------------------------------------------------------------------
# 2. 带矛盾条款的版本（Markdown）
# ---------------------------------------------------------------------------
PROCEDURE_TRACEABILITY = """# 示例公司 程序文件：标识与可追溯性控制

版本：A/0

状态：草案

## 标识与可追溯性

8.5.2 公司应对产品实施唯一标识，使标识与检验状态关联，并可追溯到材料来源。

## 标识与可追溯性（2026 年修订稿）

8.5.2 公司不再对产品实施唯一标识，改为在批次卡上由检验员手工登记 | conflict

追溯记录台账：登记批次号与检验结论的对应关系。

## 评审意见

本修订稿与现行版本在唯一标识方式上不一致，需由管理者代表裁决。
"""

# ---------------------------------------------------------------------------
# 3. 过程与场所登记簿（XLSX）
# ---------------------------------------------------------------------------
XLSX_ROWS = [
    ["条款号", "要素内容", "过程", "场所", "材料版本"],
    ["版本：A/1.0"],
    ["8.5.2", "生产过程对批次实施唯一标识并保留可追溯信息", "生产过程", "一号车间", "A/1.0"],
    ["8.5.1", "生产和服务的提供获得策划结果的确认", "生产过程", "一号车间", "A/1.0"],
    ["7.1.5", "监视测量设备溯源至国际或国家测量标准", "计量管理过程", "计量室", "A/1.0"],
    ["7.5.3", "受控文件清单与发放记录", "体系管理过程", "质量管理部", "A/1.0"],
    ["4.4", "过程管理体系的过程识别与相互作用", "体系管理过程", "质量管理部", "A/1.0"],
    ["9.9.9", "越界条款样例：条款树中不存在的条款号", "体系管理过程", "质量管理部", "A/1.0"],
]

XLSX_SHEET2_ROWS = [
    ["单值清单"],
    ["8.7 不合格输出的识别与控制点"],
    ["8.7 不合格输出的评审与处置点"],
]


def build_xlsx(path: Path) -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "登记表"
    for row in XLSX_ROWS:
        sheet.append(row)
    sheet2 = workbook.create_sheet("单值表")
    for row in XLSX_SHEET2_ROWS:
        sheet2.append(row)
    workbook.save(str(path))


# ---------------------------------------------------------------------------
# 4. 内部审核报告（DOCX）
# ---------------------------------------------------------------------------
def build_docx(path: Path) -> None:
    import docx

    document = docx.Document()
    document.add_heading("示例公司 内部审核报告（2026 年第二季度）", level=1)
    document.add_paragraph("文件版本：A/1.0")
    document.add_paragraph("审核组长：质量管理部 王某")
    document.add_heading("审核范围", level=2)
    document.add_paragraph(
        "本次审核覆盖生产过程、检验过程与文件控制过程，抽样比例为 "
        "关键过程 100%，其余过程各抽取 3 件。"
    )
    document.add_paragraph(
        "10.2 内部审核按策划的时间间隔实施，本次为第二次例行审核。"
    )
    document.add_heading("审核发现", level=2)
    document.add_paragraph(
        "8.5.2 批次追溯记录抽查 12 份，其中 2 份缺少物料批号，追溯链不完整。"
    )
    document.add_paragraph(
        "7.5.3 受控文件清单与现场在用版本核对一致，未见作废文件在现场使用。"
    )
    document.add_heading("审核结论", level=2)
    document.add_paragraph(
        "本次审核共开具 1 项不符合观察，1 项改进建议，详见下表。"
    )

    table = document.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    header = table.rows[0].cells
    for idx, name in enumerate(["序号", "条款号", "审核要素", "结论"]):
        header[idx].text = name
    for row in [
        ["1", "8.5.2", "批次记录的可追溯性", "不符合"],
        ["2", "7.5.3", "受控文件清单与在用版本一致性", "符合"],
        ["3", "8.7", "不合格输出的识别与处置", "改进建议"],
    ]:
        cells = table.add_row().cells
        for idx, value in enumerate(row):
            cells[idx].text = value

    document.save(str(path))


# ---------------------------------------------------------------------------
# 5. 无文本层扫描件（PDF，不可读）
# ---------------------------------------------------------------------------
def build_scanned_pdf(path: Path) -> None:
    try:
        import pymupdf  # type: ignore
    except ImportError:
        import fitz as pymupdf  # type: ignore

    document = pymupdf.open()
    page = document.new_page()
    # 只画图形，不写任何文字 → 页面上没有文本层（模拟未 OCR 的扫描件）
    page.draw_rect(pymupdf.Rect(60, 80, 540, 200), color=(0.2, 0.2, 0.2), fill=(0.92, 0.92, 0.92))
    page.draw_rect(pymupdf.Rect(60, 240, 540, 700), color=(0.4, 0.4, 0.4), fill=(0.86, 0.86, 0.86))
    document.save(str(path))
    document.close()


# ---------------------------------------------------------------------------
# 6. 乱码文本（TXT，不可读）
# ---------------------------------------------------------------------------
def build_garbled_txt(path: Path) -> None:
    # 私用区字符 + 控制字符：UTF-8 可解码，但内容不可辨认
    payload = "".join(chr(0xE000 + i) for i in range(16)) + "\x00\x01\x02\x03"
    path.write_text(payload, encoding="utf-8")


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "quality_manual_A1.md").write_text(QUALITY_MANUAL, encoding="utf-8")
    (FIXTURES / "procedure_traceability_A0.md").write_text(
        PROCEDURE_TRACEABILITY, encoding="utf-8"
    )
    build_xlsx(FIXTURES / "process_site_register.xlsx")
    build_docx(FIXTURES / "internal_audit_report_2026Q2.docx")
    build_scanned_pdf(FIXTURES / "supplier_cert_scan.pdf")
    build_garbled_txt(FIXTURES / "garbled_scan.txt")

    for item in sorted(FIXTURES.iterdir()):
        if item.is_file():
            print(f"{item.name}\t{item.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
