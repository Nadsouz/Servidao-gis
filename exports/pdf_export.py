# -*- coding: utf-8 -*-
"""Exportação de relatório técnico de faixa de servidão para PDF (QGIS 3.x).

O módulo recebe as camadas já processadas pelo fluxo principal e produz um PDF
com capa, resumo executivo, tabela de imóveis, gráfico por município, mapa geral
e metodologia/fontes. Não cria mapas individuais.

Integração sugerida no script principal:
    from pdf_export import exportar_relatorio_pdf
    exportar_relatorio_pdf(
        faixa=faixa, imoveis=imoveis, intersecoes=intersecoes,
        caminho_pdf=pdf, campo_id=CAMPO_ID_CAR,
        campo_nome=CAMPO_NOME_IMOVEL,
        campo_municipio="municipio", largura_total_m=LARGURA_TOTAL_M,
        modo_buffer=MODO_BUFFER,
        fontes=["Base de imóveis CAR fornecida pelo contratante", "Traçado fornecido pelo contratante"]
    )

A área geométrica da faixa é calculada sobre a camada dissolvida da faixa.
A soma das áreas de interseção é calculada por imóvel e pode ser maior que a
área geométrica da faixa quando os polígonos CAR se sobrepõem.
"""

import os
from datetime import datetime

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    QgsProject, QgsPrintLayout, QgsLayoutItemPage, QgsLayoutItemLabel,
    QgsLayoutItemMap, QgsLayoutItemShape, QgsLayoutPoint, QgsLayoutSize,
    QgsLayoutExporter, QgsRectangle, QgsFillSymbol, QgsLayoutItemLegend,
)


# Paleta discreta para impressão
AZUL_ESCURO = "#17365D"
AZUL = "#4472C4"
AZUL_CLARO = "#D9EAF7"
CINZA = "#666666"
CINZA_CLARO = "#E7E6E6"
BRANCO = "#FFFFFF"
PRETO = "#222222"


# -----------------------------------------------------------------------------
# Utilitários de layout
# -----------------------------------------------------------------------------
def _page_y(layout, y, page):
    """Converte Y relativo à página para coordenada global do layout.

    Compatível com versões do QGIS 3.x em que QgsLayoutPageCollection
    não disponibiliza pagePosition(). A posição da página é obtida do
    próprio QgsLayoutItemPage, que já está posicionado no layout.
    """
    pages = layout.pageCollection().pages()
    if page < 0 or page >= len(pages):
        raise IndexError("Página {} não existe no layout ({} páginas).".format(page + 1, len(pages)))
    return pages[page].pos().y() + y


def _add_label(layout, text, x, y, w, h, page=0, size=9, bold=False,
               color=PRETO, align=Qt.AlignLeft, valign=Qt.AlignVCenter,
               margin=1, font_family="Arial"):
    item = QgsLayoutItemLabel(layout)
    item.setText("" if text is None else str(text))
    item.setFont(QFont(font_family, size, QFont.Bold if bold else QFont.Normal))
    item.setFontColor(QColor(color))
    item.setHAlign(align)
    item.setVAlign(valign)
    item.setMargin(margin)
    layout.addLayoutItem(item)
    item.attemptMove(QgsLayoutPoint(x, _page_y(layout, y, page)))
    item.attemptResize(QgsLayoutSize(w, h))
    return item


def _add_rect(layout, x, y, w, h, page=0, fill=AZUL, stroke=None):
    item = QgsLayoutItemShape(layout)
    item.setShapeType(QgsLayoutItemShape.Rectangle)
    stroke = stroke or fill
    symbol = QgsFillSymbol.createSimple({
        "color": fill,
        "outline_color": stroke,
        "outline_width": "0.1",
        "joinstyle": "miter",
    })
    item.setSymbol(symbol)
    layout.addLayoutItem(item)
    item.attemptMove(QgsLayoutPoint(x, _page_y(layout, y, page)))
    item.attemptResize(QgsLayoutSize(w, h))
    return item


def _add_header_footer(layout, page, page_title, total_pages):
    _add_rect(layout, 0, 0, 210, 13, page, fill=AZUL_ESCURO)
    _add_label(layout, page_title, 12, 2, 185, 8, page, 10, True, BRANCO)
    _add_label(layout, "RELATÓRIO TÉCNICO | ANÁLISE DE FAIXA DE SERVIDÃO",
               12, 285, 150, 6, page, 7, False, CINZA)
    _add_label(layout, "Página {} de {}".format(page + 1, total_pages),
               165, 285, 33, 6, page, 7, False, CINZA, Qt.AlignRight)


def _fmt(value, decimals=2):
    try:
        return ("{:,." + str(decimals) + "f}").format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "—"


def _field_value(feature, field_name, default=""):
    if field_name and field_name in feature.fields().names():
        value = feature[field_name]
        return default if value is None else value
    return default


def _area_geom_ha(layer):
    return sum(f.geometry().area() for f in layer.getFeatures() if f.hasGeometry()) / 10000.0


def _collect_data(imoveis, campo_id, campo_nome="", campo_municipio=""):
    rows = []
    municipality_counts = {}
    sum_intersections = 0.0
    for feat in imoveis.getFeatures():
        codigo = str(_field_value(feat, campo_id, ""))
        nome = str(_field_value(feat, campo_nome, "")) if campo_nome else ""
        municipio = str(_field_value(feat, campo_municipio, "Não informado")) if campo_municipio else "Não informado"
        area_total = feat.geometry().area() / 10000.0 if feat.hasGeometry() else 0.0
        area_inter = _field_value(feat, "area_int_ha", 0.0)
        percentual = _field_value(feat, "perc_ating", None)
        try:
            area_inter = float(area_inter or 0.0)
        except (TypeError, ValueError):
            area_inter = 0.0
        try:
            percentual = float(percentual) if percentual is not None else (area_inter / area_total * 100.0 if area_total else None)
        except (TypeError, ValueError):
            percentual = None
        sum_intersections += area_inter
        municipality_counts[municipio] = municipality_counts.get(municipio, 0) + 1
        rows.append({
            "codigo": codigo, "nome": nome, "municipio": municipio,
            "area_total": area_total, "area_inter": area_inter,
            "percentual": percentual,
        })
    rows.sort(key=lambda r: (r["municipio"].lower(), r["codigo"].lower()))
    return rows, municipality_counts, sum_intersections


def _draw_table_page(layout, rows, page_start, page_end, page, total_pages):
    _add_header_footer(layout, page, "IMÓVEIS ATINGIDOS", total_pages)
    _add_label(layout, "Relação dos imóveis interceptados pela faixa de servidão", 12, 19, 185, 9, page, 12, True, AZUL_ESCURO)
    headers = ["Código CAR", "Imóvel", "Município", "Área do imóvel (ha)", "Interseção (ha)", "Atingido (%)"]
    widths = [39, 37, 37, 29, 29, 27]
    x0, y0, row_h = 12, 34, 8
    x = x0
    for header, width in zip(headers, widths):
        _add_rect(layout, x, y0, width, row_h, page, fill=AZUL_ESCURO)
        _add_label(layout, header, x + 0.5, y0 + 0.5, width - 1, row_h - 1, page, 6, True, BRANCO, Qt.AlignCenter)
        x += width
    y = y0 + row_h
    for index, row in enumerate(rows[page_start:page_end]):
        values = [row["codigo"], row["nome"], row["municipio"],
                  _fmt(row["area_total"]), _fmt(row["area_inter"]),
                  _fmt(row["percentual"], 2) if row["percentual"] is not None else "—"]
        x = x0
        bg = BRANCO if index % 2 == 0 else "#F3F6F9"
        for value, width in zip(values, widths):
            _add_rect(layout, x, y, width, row_h, page, fill=bg, stroke="#D0D7DE")
            _add_label(layout, value, x + 0.7, y + 0.5, width - 1.4, row_h - 1,
                       page, 6, False, PRETO, Qt.AlignLeft if width < 38 else Qt.AlignCenter)
            x += width
        y += row_h

    _add_label(layout,
        "Nota: a coluna ‘Interseção’ representa a área interceptada em cada imóvel. "
        "A soma entre imóveis pode contabilizar áreas sobrepostas entre polígonos CAR.",
        12, 270, 185, 11, page, 7, False, CINZA)


# -----------------------------------------------------------------------------
# Exportação principal
# -----------------------------------------------------------------------------
def exportar_relatorio_pdf(
    faixa,
    imoveis,
    intersecoes,
    caminho_pdf,
    campo_id,
    campo_nome="",
    campo_municipio="municipio",
    titulo="RELATÓRIO DE ANÁLISE DE FAIXA DE SERVIDÃO",
    subtitulo="Diagnóstico territorial dos imóveis interceptados",
    contratante="",
    empreendimento="",
    responsavel="",
    data_referencia=None,
    modo_buffer="Não informado",
    largura_total_m=None,
    fontes=None,
    metodologia_extra="",
    dpi=180,
    tracado=None,
):
    """Gera o relatório PDF multipágina a partir das camadas processadas.

    Args:
        faixa: polígono dissolvido da faixa de servidão, em CRS projetado/métrico.
        imoveis: camada de imóveis atingidos, com area_int_ha e perc_ating.
        intersecoes: camada resultante da interseção CAR x faixa (para rastreabilidade).
        caminho_pdf: caminho completo do PDF de saída.
        campo_id: campo identificador CAR.
        campo_nome: campo opcional com nome do imóvel.
        campo_municipio: campo com município (padrão: "municipio"). Se o campo
                         não existir na camada, o gráfico indicará "Não informado".
        fontes: lista de fontes de dados para a seção de metodologia.
        tracado: camada linear original/reprojetada do eixo do empreendimento (opcional).
    """
    if not faixa or not faixa.isValid():
        raise RuntimeError("Camada da faixa inválida para exportação do relatório.")
    if not imoveis or not imoveis.isValid():
        raise RuntimeError("Camada de imóveis atingidos inválida para exportação do relatório.")
    if campo_id not in imoveis.fields().names():
        raise RuntimeError("Campo identificador '{}' não encontrado na camada de imóveis.".format(campo_id))

    fontes = fontes or ["Camada de traçado fornecida para o estudo", "Base de imóveis CAR utilizada no processamento"]
    data_referencia = data_referencia or datetime.now().strftime("%d/%m/%Y")
    rows, municipios, soma_intersecoes_ha = _collect_data(imoveis, campo_id, campo_nome, campo_municipio)
    area_faixa_ha = _area_geom_ha(faixa)
    total_imoveis = len(rows)
    total_municipios = len(municipios)
    area_total_cadastrada = sum(r["area_total"] for r in rows)

    # Páginas: capa, resumo, tabela(s), gráfico, mapa geral e metodologia.
    linhas_por_pagina = 26
    numero_paginas_tabela = max(1, (total_imoveis + linhas_por_pagina - 1) // linhas_por_pagina)
    total_paginas = 5 + numero_paginas_tabela

    project = QgsProject.instance()
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    first_page = layout.pageCollection().pages()[0]
    first_page.setPageSize("A4", QgsLayoutItemPage.Portrait)
    for _ in range(total_paginas - 1):
        page_obj = QgsLayoutItemPage(layout)
        page_obj.setPageSize("A4", QgsLayoutItemPage.Portrait)
        layout.pageCollection().addPage(page_obj)

    # 1. CAPA PERSONALIZADA
    _add_rect(layout, 0, 0, 210, 297, 0, fill="#F5F7FA")
    _add_rect(layout, 0, 0, 210, 76, 0, fill=AZUL_ESCURO)
    _add_label(layout, "ESTUDO TERRITORIAL", 16, 17, 178, 10, 0, 10, True, "#BFD7EE")
    _add_label(layout, titulo, 16, 43, 178, 25, 0, 21, True, BRANCO)
    _add_label(layout, subtitulo, 16, 91, 178, 18, 0, 13, False, AZUL_ESCURO)
    _add_rect(layout, 16, 119, 178, 1.2, 0, fill=AZUL)
    capa_info = []
    if contratante:
        capa_info.append(("CONTRATANTE", contratante))
    if empreendimento:
        capa_info.append(("EMPREENDIMENTO", empreendimento))
    if responsavel:
        capa_info.append(("RESPONSÁVEL TÉCNICO", responsavel))
    capa_info.append(("DATA DE REFERÊNCIA", data_referencia))
    y = 137
    for label, value in capa_info:
        _add_label(layout, label, 17, y, 170, 6, 0, 8, True, CINZA)
        _add_label(layout, value, 17, y + 7, 175, 10, 0, 11, False, PRETO)
        y += 26
    _add_label(layout, "Análise espacial de imóveis interceptados por faixa de servidão",
               17, 257, 175, 12, 0, 8, False, CINZA)

    # 2. RESUMO EXECUTIVO E INDICADORES
    p = 1
    _add_header_footer(layout, p, "RESUMO EXECUTIVO", total_paginas)
    _add_label(layout, "Síntese dos resultados", 12, 20, 185, 10, p, 14, True, AZUL_ESCURO)
    indicadores = [
        ("IMÓVEIS ATINGIDOS", str(total_imoveis)),
        ("MUNICÍPIOS", str(total_municipios)),
        ("ÁREA GEOMÉTRICA DA FAIXA", _fmt(area_faixa_ha) + " ha"),
        ("SOMA DAS INTERSEÇÕES", _fmt(soma_intersecoes_ha) + " ha"),
    ]
    card_w, card_h, gap = 86, 29, 6
    for i, (label, value) in enumerate(indicadores):
        col, row = i % 2, i // 2
        x, y = 12 + col * (card_w + gap), 39 + row * (card_h + 7)
        _add_rect(layout, x, y, card_w, card_h, p, fill=AZUL_CLARO, stroke="#B4C7E7")
        _add_label(layout, label, x + 3, y + 3, card_w - 6, 7, p, 7, True, AZUL_ESCURO)
        _add_label(layout, value, x + 3, y + 11, card_w - 6, 14, p, 16, True, AZUL_ESCURO)

    _add_label(layout, "Interpretação das áreas", 12, 119, 185, 9, p, 11, True, AZUL_ESCURO)
    resumo_area = (
        "A área geométrica da faixa corresponde ao polígono da faixa de servidão após a dissolução. "
        "A soma das áreas de interseção corresponde à soma das parcelas interceptadas em cada imóvel CAR. "
        "Os valores não são necessariamente iguais: sobreposições entre imóveis CAR podem fazer com que "
        "uma mesma porção espacial seja contabilizada em mais de um imóvel. Portanto, a soma das "
        "interseções não deve ser interpretada como área territorial única da faixa."
    )
    _add_label(layout, resumo_area, 12, 132, 185, 49, p, 9, False, PRETO, Qt.AlignLeft, Qt.AlignTop)
    _add_label(layout, "Outros dados de contexto", 12, 190, 185, 9, p, 11, True, AZUL_ESCURO)
    _add_label(layout, "Área total dos imóveis atingidos (geometrias integrais): {} ha".format(_fmt(area_total_cadastrada)),
               12, 203, 185, 8, p, 9, False, PRETO)
    _add_label(layout, "Modo de geração da faixa: {}".format(modo_buffer), 12, 214, 185, 8, p, 9, False, PRETO)
    if largura_total_m is not None:
        _add_label(layout, "Largura total informada: {} m".format(_fmt(largura_total_m, 2)),
                   12, 225, 185, 8, p, 9, False, PRETO)
    _add_label(layout, "Observação: os indicadores dependem da qualidade geométrica e atributiva das bases de entrada.",
               12, 250, 185, 14, p, 8, False, CINZA)

    # 3. TABELA DE IMÓVEIS (uma ou mais páginas)
    for table_i in range(numero_paginas_tabela):
        page_num = 2 + table_i
        start = table_i * linhas_por_pagina
        end = min(start + linhas_por_pagina, total_imoveis)
        _draw_table_page(layout, rows, start, end, page_num, total_paginas)
        if total_imoveis == 0:
            _add_label(layout, "Nenhum imóvel atingido foi identificado.", 12, 48, 185, 12, page_num, 9, False, CINZA)

    # Página seguinte ao bloco de tabela
    p_grafico = 2 + numero_paginas_tabela
    _add_header_footer(layout, p_grafico, "IMÓVEIS POR MUNICÍPIO", total_paginas)
    _add_label(layout, "Distribuição quantitativa dos imóveis atingidos", 12, 20, 185, 10, p_grafico, 13, True, AZUL_ESCURO)
    ordered = sorted(municipios.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    max_items = 15
    shown = ordered[:max_items]
    if len(ordered) > max_items:
        outros = sum(v for _, v in ordered[max_items:])
        shown.append(("Demais municípios", outros))
    max_count = max([v for _, v in shown], default=1)
    chart_x, chart_y, chart_w, bar_h, row_gap = 56, 43, 125, 8, 7
    if not shown:
        _add_label(layout, "Não há dados municipais disponíveis para representar.", 12, 48, 185, 12, p_grafico, 9, False, CINZA)
    for idx, (municipio, count) in enumerate(shown):
        y = chart_y + idx * (bar_h + row_gap)
        _add_label(layout, municipio[:30], 12, y, 42, bar_h, p_grafico, 7, False, PRETO)
        bar_width = max(1.5, chart_w * count / max_count)
        _add_rect(layout, chart_x, y + 1, bar_width, bar_h - 2, p_grafico, fill=AZUL)
        _add_label(layout, str(count), chart_x + bar_width + 2, y, 18, bar_h, p_grafico, 8, True, AZUL_ESCURO)
    _add_label(layout, "Total de imóveis representados: {}".format(total_imoveis),
               12, 264, 185, 8, p_grafico, 9, True, AZUL_ESCURO)

    # 4. MAPA GERAL ÚNICO DO TRAÇADO E DA FAIXA
    p_mapa = p_grafico + 1
    _add_header_footer(layout, p_mapa, "MAPA GERAL DO TRAÇADO E DA FAIXA", total_paginas)
    _add_label(layout, "Visão geral da faixa de servidão e dos imóveis atingidos", 12, 19, 185, 9, p_mapa, 12, True, AZUL_ESCURO)
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(12, _page_y(layout, 34, p_mapa)))
    map_item.attemptResize(QgsLayoutSize(186, 220))

    # Define explicitamente o CRS do mapa e calcula uma extensão conjunta.
    # Isso evita enquadramentos vazios quando a extensão da faixa é muito estreita
    # ou quando os imóveis ocupam uma área maior que o próprio corredor.
    map_extent = QgsRectangle(faixa.extent())
    map_extent.combineExtentWith(imoveis.extent())

    map_layers = [imoveis, faixa]
    if tracado is not None and tracado.isValid() and tracado.featureCount() > 0:
        map_extent.combineExtentWith(tracado.extent())
        # O eixo é incluído por último para permanecer visível no mapa final.
        map_layers.append(tracado)

    if map_extent.isNull() or map_extent.isEmpty():
        raise RuntimeError("Não foi possível calcular a extensão espacial do mapa geral.")

    map_extent.scale(1.12)
    map_item.setCrs(faixa.crs())
    map_item.zoomToExtent(map_extent)
    map_item.setLayers(map_layers)
    map_item.setKeepLayerSet(True)
    map_item.setBackgroundEnabled(True)
    map_item.setBackgroundColor(QColor(BRANCO))
    map_item.setFrameEnabled(True)
    map_item.refresh()
    _add_label(layout,
        "Mapa único de localização do traçado e da faixa. A representação não substitui a conferência "
        "dos limites cadastrais nem a análise individual dos documentos dos imóveis.",
        12, 260, 185, 16, p_mapa, 8, False, CINZA, Qt.AlignLeft, Qt.AlignTop)

    # 5. METODOLOGIA E FONTES
    p_metodo = p_mapa + 1
    _add_header_footer(layout, p_metodo, "METODOLOGIA E FONTES DOS DADOS", total_paginas)
    _add_label(layout, "Procedimentos metodológicos", 12, 20, 185, 10, p_metodo, 13, True, AZUL_ESCURO)
    metodologia = [
        "1. Carregamento das camadas vetoriais do traçado e dos imóveis cadastrados no CAR.",
        "2. Reprojeção das camadas para um sistema de referência projetado e métrico, adequado ao cálculo de distâncias e áreas.",
        "3. Verificação e correção de geometrias inválidas antes das operações espaciais.",
        "4. Geração da faixa de servidão por buffer: modo {}.".format(modo_buffer),
        "5. Dissolução das geometrias da faixa para representar a área geométrica contínua do corredor.",
        "6. Seleção dos imóveis que intersectam a faixa e cálculo das geometrias de interseção por imóvel.",
        "7. Cálculo da área interceptada e do percentual em relação à área integral de cada imóvel.",
        "8. Consolidação dos indicadores, tabela, gráfico municipal e mapa geral em relatório PDF.",
    ]
    y = 36
    for line in metodologia:
        _add_label(layout, line, 14, y, 182, 14, p_metodo, 8, False, PRETO, Qt.AlignLeft, Qt.AlignTop)
        y += 18
    if metodologia_extra:
        _add_label(layout, "Informações complementares", 12, y + 2, 185, 8, p_metodo, 10, True, AZUL_ESCURO)
        _add_label(layout, metodologia_extra, 14, y + 13, 182, 24, p_metodo, 8, False, PRETO, Qt.AlignLeft, Qt.AlignTop)
        y += 42
    _add_label(layout, "Fontes dos dados", 12, y + 3, 185, 9, p_metodo, 11, True, AZUL_ESCURO)
    y += 15
    for fonte in fontes:
        _add_label(layout, "• " + str(fonte), 14, y, 182, 11, p_metodo, 8, False, PRETO, Qt.AlignLeft, Qt.AlignTop)
        y += 13
    _add_label(layout,
        "Limitações e ressalvas: os resultados dependem da escala, atualidade, precisão posicional e consistência "
        "topológica das bases utilizadas. A soma das áreas de interseção por imóvel pode incluir sobreposições "
        "entre cadastros CAR; por isso, não representa necessariamente uma área espacial exclusiva.",
        12, 254, 185, 25, p_metodo, 8, False, CINZA, Qt.AlignLeft, Qt.AlignTop)

    os.makedirs(os.path.dirname(os.path.abspath(caminho_pdf)), exist_ok=True)
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = int(dpi)
    result = exporter.exportToPdf(caminho_pdf, settings)
    if result != QgsLayoutExporter.Success:
        raise RuntimeError("Falha ao exportar relatório PDF. Código: {}".format(result))

    return {
        "caminho_pdf": caminho_pdf,
        "total_imoveis": total_imoveis,
        "total_municipios": total_municipios,
        "area_geometrica_faixa_ha": area_faixa_ha,
        "soma_intersecoes_imoveis_ha": soma_intersecoes_ha,
        "paginas": total_paginas,
    }
