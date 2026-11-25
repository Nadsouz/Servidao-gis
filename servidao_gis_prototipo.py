# -*- coding: utf-8 -*-
"""
Servidao GIS - prototipo funcional para executar no Console Python do QGIS.

Requisitos:
- QGIS 3.x com Processing habilitado
- openpyxl instalado no Python utilizado pelo QGIS (para gerar XLSX)

Como usar:
1. Edite o bloco CONFIGURACAO.
2. Abra o QGIS e carregue/tenha acesso aos arquivos de entrada.
3. Abra Plugins > Console Python.
4. Execute: exec(open(r"C:/caminho/servidao_gis_prototipo.py", encoding="utf-8").read())

Modo de buffer:
- "fixo": usa largura_total_m para todo o traçado.
- "variavel": usa uma camada de linhas segmentadas com campo largura_m.
  Cada feição/segmento recebe sua própria largura total.

A largura informada é TOTAL. Ex.: 60 m = 30 m para cada lado do eixo.
"""

from qgis.core import (
    QgsApplication, QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsField, QgsFields, QgsWkbTypes, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsVectorFileWriter, QgsPrintLayout,
    QgsLayoutItemMap, QgsLayoutItemPage, QgsLayoutItemLabel, QgsLayoutSize,
    QgsLayoutPoint, QgsLayoutExporter, QgsRectangle, QgsUnitTypes
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QFont
import processing
import os
import csv
import math
import traceback
import sys

sys.path.insert(0, r"E:\plugin_servidao\servidao_gis")

from pdf_export import exportar_relatorio_pdf


# ========================= CONFIGURACAO =========================

KML_TRAÇADO = r"E:\plugin_servidao\tracado.kml"
CAR_ARQUIVO = r"E:\plugin_servidao\base_car.zip"
CAR_CAMADA = ""                 # vazio = primeira camada válida; ou informe o nome
CAMADA_SEGMENTOS = ""           # caminho opcional da camada de segmentos para buffer variável
CAMPO_LARGURA = "largura_m"     # largura TOTAL de cada segmento, em metros

MODO_BUFFER = "fixo"            # "fixo" ou "variavel"
LARGURA_TOTAL_M = 60.0          # usada no modo fixo

CAMPO_ID_CAR = "cod_imovel"     # ajuste para o nome real do identificador na sua base
CAMPO_NOME_IMOVEL = ""          # opcional; ex.: "nom_imovel"
PASTA_SAIDA = r"C:\Projeto\resultado_servidao"

# CRS opcional. None = estimar UTM pelo centro do traçado.
# Exemplo: "EPSG:31983" (SIRGAS 2000 / UTM 23S).
CRS_METRICO_FIXO = None

# ================================================================


def log(msg):
    print("[Servidao GIS] " + str(msg))


def criar_pasta(path):
    os.makedirs(path, exist_ok=True)


def carregar_vetor(caminho, nome="camada", provider="ogr"):
    layer = QgsVectorLayer(caminho, nome, provider)
    if not layer.isValid():
        raise RuntimeError("Não foi possível abrir a camada: " + caminho)
    return layer


def escolher_crs_metrico(layer):
    """Estima zona UTM pelo centroide do extent. Para áreas extensas, definir CRS manualmente."""
    if CRS_METRICO_FIXO:
        crs = QgsCoordinateReferenceSystem(CRS_METRICO_FIXO)
        if not crs.isValid():
            raise RuntimeError("CRS_METRICO_FIXO inválido.")
        return crs

    source = layer.crs()
    if not source.isValid():
        raise RuntimeError("A camada de entrada não possui CRS válido.")

    project_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    transform = QgsCoordinateTransform(source, project_crs, QgsProject.instance())
    center = layer.extent().center()
    center_wgs = transform.transform(center)
    lon, lat = center_wgs.x(), center_wgs.y()
    zone = int(math.floor((lon + 180) / 6) + 1)
    zone = max(1, min(60, zone))
    epsg = (32700 + zone) if lat < 0 else (32600 + zone)
    crs = QgsCoordinateReferenceSystem("EPSG:" + str(epsg))
    log("CRS métrico estimado: EPSG:" + str(epsg))
    return crs


def reprojetar(layer, crs_destino):
    result = processing.run(
        "native:reprojectlayer",
        {"INPUT": layer, "TARGET_CRS": crs_destino, "OUTPUT": "memory:"}
    )["OUTPUT"]
    if not result.isValid():
        raise RuntimeError("Falha ao reprojetar " + layer.name())
    return result


def salvar_gpkg(layer, caminho, nome, first=False):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = nome
    options.fileEncoding = "UTF-8"
    if not first and os.path.exists(caminho):
        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
    else:
        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, caminho, QgsProject.instance().transformContext(), options
    )
    if result[0] != QgsVectorFileWriter.NoError:
        raise RuntimeError("Erro ao salvar GPKG: " + str(result))
    return caminho

def reparar_geometrias(layer, nome):
    """Corrige geometrias inválidas antes das operações espaciais."""

    log("Corrigindo geometrias: " + nome)

    resultado = processing.run(
        "native:fixgeometries",
        {
            "INPUT": layer,
            "OUTPUT": "memory:"
        }
    )["OUTPUT"]

    if not resultado or not resultado.isValid():
        raise RuntimeError(
            "Falha ao corrigir geometrias: " + nome
        )

    resultado.setName(nome + "_corrigida")

    log(
        "Correção concluída: {} feições".format(
            resultado.featureCount()
        )
    )

    return resultado


def criar_buffer_fixo(linha, largura_total):
    if largura_total <= 0:
        raise ValueError("A largura total do buffer deve ser maior que zero.")
    result = processing.run(
        "native:buffer",
        {
            "INPUT": linha,
            "DISTANCE": float(largura_total) / 2.0,
            "SEGMENTS": 8,
            "END_CAP_STYLE": 0,
            "JOIN_STYLE": 0,
            "MITER_LIMIT": 2,
            "DISSOLVE": True,
            "OUTPUT": "memory:"
        }
    )["OUTPUT"]
    return result


def criar_buffer_variavel(segmentos, campo_largura):
    nomes = [f.name() for f in segmentos.fields()]
    if campo_largura not in nomes:
        raise RuntimeError("A camada de segmentos não possui o campo '" + campo_largura + "'.")

    # Cria buffer por feição com distância própria (metade da largura total).
    fields = QgsFields()
    fields.append(QgsField("largura_m", QVariant.Double))
    mem = QgsVectorLayer("MultiPolygon?crs=" + segmentos.crs().authid(), "buffer_variavel", "memory")
    prov = mem.dataProvider()
    prov.addAttributes(list(fields))
    mem.updateFields()

    novas = []
    for feat in segmentos.getFeatures():
        geom = feat.geometry()
        if not geom or geom.isEmpty():
            continue
        try:
            largura = float(feat[campo_largura])
        except Exception:
            raise RuntimeError("Valor inválido no campo " + campo_largura + " (feição " + str(feat.id()) + ").")
        if largura <= 0:
            raise RuntimeError("A largura deve ser maior que zero (feição " + str(feat.id()) + ").")
        buffered = geom.buffer(largura / 2.0, 8)
        if not buffered or buffered.isEmpty():
            continue
        out = QgsFeature(mem.fields())
        out.setGeometry(buffered)
        out["largura_m"] = largura
        novas.append(out)

    prov.addFeatures(novas)
    mem.updateExtents()
    if mem.featureCount() == 0:
        raise RuntimeError("Nenhum buffer foi gerado no modo variável.")

    dissolved = processing.run(
        "native:dissolve",
        {"INPUT": mem, "FIELD": [], "SEPARATE_DISJOINT": False, "OUTPUT": "memory:"}
    )["OUTPUT"]
    return dissolved


def campo_valor(feat, nome):
    if nome and nome in feat.fields().names():
        return feat[nome]
    return None


def construir_camadas_resultado(car, faixa, campo_id):
    """Retorna imóveis selecionados e polígonos de interseção com métricas."""
    if campo_id not in car.fields().names():
        raise RuntimeError(
            "Campo identificador CAR '" + campo_id + "' não encontrado. "
            "Edite CAMPO_ID_CAR no início do script. Campos disponíveis: "
            + ", ".join(car.fields().names())
        )

    selecionados = processing.run(
        "native:extractbylocation",
        {
            "INPUT": car,
            "PREDICATE": [0],  # intersects
            "INTERSECT": faixa,
            "OUTPUT": "memory:"
        }
    )["OUTPUT"]

    # Camada de interseção para armazenar geometrias atingidas.
    inter = processing.run(
        "native:intersection",
        {"INPUT": car, "OVERLAY": faixa, "INPUT_FIELDS": [], "OVERLAY_FIELDS": [],
         "OVERLAY_FIELDS_PREFIX": "faixa_", "OUTPUT": "memory:"}
    )["OUTPUT"]

    # Soma as áreas de interseção por código CAR, evitando duplicidade na tabela final.
    areas_por_id = {}
    for f in inter.getFeatures():
        codigo = str(f[campo_id])
        area_ha = f.geometry().area() / 10000.0
        areas_por_id[codigo] = areas_por_id.get(codigo, 0.0) + area_ha

    # Monta camada de imóveis atingidos com atributos originais + métricas.
    out = QgsVectorLayer(
        QgsWkbTypes.displayString(car.wkbType()) + "?crs=" + car.crs().authid(),
        "imoveis_atingidos", "memory"
    )
    provider = out.dataProvider()
    new_fields = list(car.fields())
    new_fields.extend([
        QgsField("area_int_ha", QVariant.Double),
        QgsField("perc_ating", QVariant.Double)
    ])
    provider.addAttributes(new_fields)
    out.updateFields()

    for f in selecionados.getFeatures():
        codigo = str(f[campo_id])
        area_inter = areas_por_id.get(codigo, 0.0)
        area_total = f.geometry().area() / 10000.0
        percentual = (area_inter / area_total * 100.0) if area_total > 0 else None
        nf = QgsFeature(out.fields())
        nf.setGeometry(f.geometry())
        attrs = f.attributes() + [area_inter, percentual]
        nf.setAttributes(attrs)
        provider.addFeature(nf)

    out.updateExtents()
    return out, inter, areas_por_id


def exportar_excel(layer, caminho, campo_id, campo_nome=""):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise RuntimeError(
            "A biblioteca openpyxl não está instalada no Python do QGIS. "
            "Instale-a no ambiente Python do QGIS e execute novamente."
        )

    wb = Workbook()
    ws = wb.active
    ws.title = "Imoveis atingidos"

    headers = ["codigo_car"]
    if campo_nome and campo_nome in layer.fields().names():
        headers.append("nome_imovel")
    headers.extend(["area_interceptada_ha", "percentual_atingido"])
    # Acrescenta os demais atributos originais, sem repetir campos calculados.
    for fld in layer.fields():
        if fld.name() not in headers and fld.name() not in ("area_int_ha", "perc_ating"):
            headers.append(fld.name())

    ws.append(headers)
    for f in layer.getFeatures():
        row = []
        for h in headers:
            if h == "codigo_car":
                row.append(str(f[campo_id]))
            elif h == "nome_imovel":
                row.append(str(f[campo_nome]) if f[campo_nome] is not None else "")
            elif h == "area_interceptada_ha":
                row.append(round(float(f["area_int_ha"] or 0), 6))
            elif h == "percentual_atingido":
                row.append(round(float(f["perc_ating"] or 0), 4))
            else:
                value = f[h] if h in f.fields().names() else ""
                row.append(str(value) if value is not None else "")
        ws.append(row)

    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[letter].width = min(max(max_len + 2, 12), 42)

    wb.save(caminho)


def exportar_layout(faixa, imoveis, caminho_pdf, caminho_png, total_imoveis, area_faixa):
    project = QgsProject.instance()
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    page = layout.pageCollection().pages()[0]
    page.setPageSize("A4", QgsLayoutItemPage.Landscape)

    title = QgsLayoutItemLabel(layout)
    title.setText("ANÁLISE DE FAIXA DE SERVIDÃO")
    title.setFont(QFont("Arial", 18, QFont.Bold))
    title.adjustSizeToText()
    layout.addLayoutItem(title)
    title.attemptMove(QgsLayoutPoint(10, 8))

    subtitle = QgsLayoutItemLabel(layout)
    subtitle.setText(
        "Imóveis identificados: {}   |   Área da faixa: {:.2f} ha".format(
            total_imoveis, area_faixa
        )
    )
    subtitle.setFont(QFont("Arial", 10))
    subtitle.adjustSizeToText()
    layout.addLayoutItem(subtitle)
    subtitle.attemptMove(QgsLayoutPoint(10, 20))

    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(10, 32))
    map_item.attemptResize(QgsLayoutSize(277, 160))
    extent = faixa.extent()
    extent.scale(1.15)
    map_item.zoomToExtent(extent)
    map_item.setLayers([imoveis, faixa])
    map_item.setKeepLayerSet(True)
    map_item.refresh()

    exporter = QgsLayoutExporter(layout)
    pdf_settings = QgsLayoutExporter.PdfExportSettings()
    pdf_settings.dpi = 180
    result_pdf = exporter.exportToPdf(caminho_pdf, pdf_settings)
    if result_pdf != QgsLayoutExporter.Success:
        raise RuntimeError("Falha ao exportar PDF. Código: " + str(result_pdf))

    image_settings = QgsLayoutExporter.ImageExportSettings()
    image_settings.dpi = 180
    result_img = exporter.exportToImage(caminho_png, image_settings)
    if result_img != QgsLayoutExporter.Success:
        raise RuntimeError("Falha ao exportar mapa PNG. Código: " + str(result_img))


def executar():
    criar_pasta(PASTA_SAIDA)
    log("Iniciando processamento...")

    linha_original = carregar_vetor(KML_TRAÇADO, "tracado")
    car_original = carregar_vetor(CAR_ARQUIVO, "CAR")

    if QgsWkbTypes.geometryType(linha_original.wkbType()) != QgsWkbTypes.LineGeometry:
        raise RuntimeError("O KML precisa conter geometria linear (linha/traçado).")
    if QgsWkbTypes.geometryType(car_original.wkbType()) != QgsWkbTypes.PolygonGeometry:
        raise RuntimeError("A camada CAR precisa conter polígonos.")

    crs_metrico = escolher_crs_metrico(linha_original)
    linha = reprojetar(linha_original, crs_metrico)
    car = reprojetar(car_original, crs_metrico)

# Correção automática das geometrias
    linha = reparar_geometrias(linha, "tracado")
    car = reparar_geometrias(car, "CAR")

    if MODO_BUFFER.lower() == "fixo":
        faixa = criar_buffer_fixo(linha, LARGURA_TOTAL_M)
    elif MODO_BUFFER.lower() == "variavel":
        if not CAMADA_SEGMENTOS:
            raise RuntimeError("Informe CAMADA_SEGMENTOS para o modo variável.")
        segmentos_original = carregar_vetor(CAMADA_SEGMENTOS, "segmentos")
        segmentos = reprojetar(segmentos_original, crs_metrico)
        faixa = criar_buffer_variavel(segmentos, CAMPO_LARGURA)
    else:
        raise RuntimeError("MODO_BUFFER deve ser 'fixo' ou 'variavel'.")

    faixa = reparar_geometrias(
    faixa,
    "faixa_servidao")

    faixa.setName("faixa_servidao")
    imoveis, intersecoes, areas = construir_camadas_resultado(car, faixa, CAMPO_ID_CAR)
    imoveis.setName("imoveis_atingidos")
    intersecoes.setName("intersecoes")

    # Área total da faixa após dissolução.
    area_faixa_ha = sum(f.geometry().area() for f in faixa.getFeatures()) / 10000.0
    total = imoveis.featureCount()

    gpkg = os.path.join(PASTA_SAIDA, "analise_servidao.gpkg")
    xlsx = os.path.join(PASTA_SAIDA, "imoveis_atingidos.xlsx")
    pdf = os.path.join(PASTA_SAIDA, "relatorio_servidao.pdf")
    png = os.path.join(PASTA_SAIDA, "mapa_servidao.png")

    if os.path.exists(gpkg):
        os.remove(gpkg)
    salvar_gpkg(faixa, gpkg, "faixa_servidao", first=True)
    salvar_gpkg(imoveis, gpkg, "imoveis_atingidos")
    salvar_gpkg(intersecoes, gpkg, "intersecoes")
    exportar_excel(imoveis, xlsx, CAMPO_ID_CAR, CAMPO_NOME_IMOVEL)
    exportar_relatorio_pdf(
    faixa=faixa,
    imoveis=imoveis,
    intersecoes=intersecoes,
    caminho_pdf=pdf,
    campo_id=CAMPO_ID_CAR,
    campo_nome=CAMPO_NOME_IMOVEL,
    campo_municipio="municipio",
    modo_buffer=MODO_BUFFER,
    largura_total_m=LARGURA_TOTAL_M,
    fontes=[
        "Base de imóveis do Cadastro Ambiental Rural (CAR)",
        "Traçado fornecido para análise",],
        tracado=linha)

    # Adiciona camadas ao projeto para inspeção visual.
    project = QgsProject.instance()
    project.addMapLayer(faixa)
    project.addMapLayer(imoveis)
    project.addMapLayer(intersecoes)

    log("Processamento concluído.")
    log("Imóveis identificados: {}".format(total))
    log("Área da faixa: {:.3f} ha".format(area_faixa_ha))
    log("Excel: " + xlsx)
    log("GeoPackage: " + gpkg)
    log("PDF: " + pdf)
    log("Mapa: " + png)


try:
    executar()
except Exception as exc:
    log("ERRO: " + str(exc))
    traceback.print_exc()
