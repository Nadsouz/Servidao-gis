# -*- coding: utf-8 -*-
def exportar_excel(layer, caminho, campo_id, campo_nome=""):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise RuntimeError(
            "A biblioteca openpyxl não está instalada no Python utilizado pelo QGIS. "
            "Instale-a no ambiente Python do QGIS e tente novamente."
        )

    wb = Workbook()
    ws = wb.active
    ws.title = "Imoveis atingidos"

    headers = ["codigo_car"]
    if campo_nome and campo_nome in layer.fields().names():
        headers.append("nome_imovel")
    headers.extend(["area_interceptada_ha", "percentual_atingido"])
    for fld in layer.fields():
        if fld.name() not in headers and fld.name() not in ("area_int_ha", "perc_ating"):
            headers.append(fld.name())

    ws.append(headers)
    for feature in layer.getFeatures():
        row = []
        for header in headers:
            if header == "codigo_car":
                row.append(str(feature[campo_id]))
            elif header == "nome_imovel":
                value = feature[campo_nome]
                row.append(str(value) if value is not None else "")
            elif header == "area_interceptada_ha":
                row.append(round(float(feature["area_int_ha"] or 0), 6))
            elif header == "percentual_atingido":
                row.append(round(float(feature["perc_ating"] or 0), 4))
            else:
                value = feature[header] if header in feature.fields().names() else ""
                row.append(str(value) if value is not None else "")
        ws.append(row)

    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for column in ws.columns:
        letter = get_column_letter(column[0].column)
        max_len = max((len(str(cell.value or "")) for cell in column), default=10)
        ws.column_dimensions[letter].width = min(max(max_len + 2, 12), 42)

    wb.save(caminho)
