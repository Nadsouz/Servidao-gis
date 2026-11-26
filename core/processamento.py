# -*- coding: utf-8 -*-
import math
import os
import traceback

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsField, QgsFields,
    QgsWkbTypes, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsVectorFileWriter
)
from qgis.PyQt.QtCore import QVariant

import processing
from processing.core.Processing import Processing

from ..exports.excel_export import exportar_excel
from ..exports.pdf_export import exportar_relatorio_pdf


def _log(callback, mensagem):
    mensagem = "[Servidão GIS] " + str(mensagem)
    if callback:
        callback(mensagem)
    else:
        print(mensagem)


def _inicializar_processing():
    Processing.initialize()


def carregar_vetor(caminho, nome="camada", camada_interna=""):
    uri = caminho
    if camada_interna:
        uri = caminho + "|layername=" + camada_interna
    layer = QgsVectorLayer(uri, nome, "ogr")
    if not layer.isValid():
        raise RuntimeError(
            "Não foi possível abrir a camada: {}\n"
            "Verifique o formato, o caminho e, se necessário, o nome da camada.".format(caminho)
        )
    return layer


def escolher_crs_metrico(layer, crs_fixo=None):
    if crs_fixo:
        crs = QgsCoordinateReferenceSystem(crs_fixo)
        if not crs.isValid():
            raise RuntimeError("O CRS métrico informado é inválido: " + str(crs_fixo))
        return crs

    source = layer.crs()
    if not source.isValid():
        raise RuntimeError("A camada de entrada não possui CRS válido.")

    project_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    transform = QgsCoordinateTransform(source, project_crs, QgsProject.instance())
    center = transform.transform(layer.extent().center())
    lon, lat = center.x(), center.y()
    zone = int(math.floor((lon + 180) / 6) + 1)
    zone = max(1, min(60, zone))
    epsg = (32700 + zone) if lat < 0 else (32600 + zone)
    crs = QgsCoordinateReferenceSystem("EPSG:" + str(epsg))
    if not crs.isValid():
        raise RuntimeError("Não foi possível determinar um CRS UTM válido.")
    return crs


def reprojetar(layer, crs_destino):
    result = processing.run(
        "native:reprojectlayer",
        {"INPUT": layer, "TARGET_CRS": crs_destino, "OUTPUT": "memory:"}
    )["OUTPUT"]
    if not result or not result.isValid():
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
        raise RuntimeError("Erro ao salvar GeoPackage: " + str(result))
    return caminho


def reparar_geometrias(layer, nome, callback=None):
    _log(callback, "Corrigindo geometrias: " + nome)
    resultado = processing.run(
        "native:fixgeometries",
        {"INPUT": layer, "OUTPUT": "memory:"}
    )["OUTPUT"]
    if not resultado or not resultado.isValid():
        raise RuntimeError("Falha ao corrigir geometrias: " + nome)
    resultado.setName(nome + "_corrigida")
    _log(callback, "Correção concluída: {} feições".format(resultado.featureCount()))
    return resultado


def criar_buffer_fixo(linha, largura_total):
    if largura_total <= 0:
        raise ValueError("A largura total do buffer deve ser maior que zero.")
    return processing.run(
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


def criar_buffer_variavel(segmentos, campo_largura):
    if campo_largura not in segmentos.fields().names():
        raise RuntimeError(
            "A camada de segmentos não possui o campo '{}'. Campos disponíveis: {}".format(
                campo_largura, ", ".join(segmentos.fields().names())
            )
        )

    fields = QgsFields()
    fields.append(QgsField("largura_m", QVariant.Double))
    mem = QgsVectorLayer(
        "MultiPolygon?crs=" + segmentos.crs().authid(),
        "buffer_variavel", "memory"
    )
    provider = mem.dataProvider()
    provider.addAttributes(list(fields))
    mem.updateFields()

    novas = []
    for feat in segmentos.getFeatures():
        geom = feat.geometry()
        if not geom or geom.isEmpty():
            continue
        try:
            largura = float(feat[campo_largura])
        except Exception:
            raise RuntimeError(
                "Valor inválido no campo {} (feição {}).".format(campo_largura, feat.id())
            )
        if largura <= 0:
            raise RuntimeError(
                "A largura deve ser maior que zero (feição {}).".format(feat.id())
            )
        buffered = geom.buffer(largura / 2.0, 8)
        if not buffered or buffered.isEmpty():
            continue
        out = QgsFeature(mem.fields())
        out.setGeometry(buffered)
        out["largura_m"] = largura
        novas.append(out)

    provider.addFeatures(novas)
    mem.updateExtents()
    if mem.featureCount() == 0:
        raise RuntimeError("Nenhum buffer foi gerado no modo variável.")

    return processing.run(
        "native:dissolve",
        {"INPUT": mem, "FIELD": [], "SEPARATE_DISJOINT": False, "OUTPUT": "memory:"}
    )["OUTPUT"]


def construir_camadas_resultado(car, faixa, campo_id):
    if campo_id not in car.fields().names():
        raise RuntimeError(
            "Campo identificador CAR '{}' não encontrado. Campos disponíveis: {}".format(
                campo_id, ", ".join(car.fields().names())
            )
        )

    selecionados = processing.run(
        "native:extractbylocation",
        {
            "INPUT": car,
            "PREDICATE": [0],
            "INTERSECT": faixa,
            "OUTPUT": "memory:"
        }
    )["OUTPUT"]

    inter = processing.run(
        "native:intersection",
        {
            "INPUT": car,
            "OVERLAY": faixa,
            "INPUT_FIELDS": [],
            "OVERLAY_FIELDS": [],
            "OVERLAY_FIELDS_PREFIX": "faixa_",
            "OUTPUT": "memory:"
        }
    )["OUTPUT"]

    # Mantém a regra do protótipo: soma a área interceptada por código CAR.
    areas_por_id = {}
    for feature in inter.getFeatures():
        codigo = str(feature[campo_id])
        area_ha = feature.geometry().area() / 10000.0
        areas_por_id[codigo] = areas_por_id.get(codigo, 0.0) + area_ha

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

    for feature in selecionados.getFeatures():
        codigo = str(feature[campo_id])
        area_inter = areas_por_id.get(codigo, 0.0)
        area_total = feature.geometry().area() / 10000.0
        percentual = (area_inter / area_total * 100.0) if area_total > 0 else None
        new_feature = QgsFeature(out.fields())
        new_feature.setGeometry(feature.geometry())
        new_feature.setAttributes(feature.attributes() + [area_inter, percentual])
        provider.addFeature(new_feature)

    out.updateExtents()
    return out, inter, areas_por_id


def executar_analise(config, callback=None):
    _inicializar_processing()
    pasta_saida = config["pasta_saida"]
    os.makedirs(pasta_saida, exist_ok=True)
    _log(callback, "Carregando os dados de entrada…")

    linha_original = carregar_vetor(config["tracado"], "tracado")
    car_original = carregar_vetor(
        config["car"], "CAR", config.get("car_camada", "")
    )

    if QgsWkbTypes.geometryType(linha_original.wkbType()) != QgsWkbTypes.LineGeometry:
        raise RuntimeError("O arquivo do traçado precisa conter geometria linear.")
    if QgsWkbTypes.geometryType(car_original.wkbType()) != QgsWkbTypes.PolygonGeometry:
        raise RuntimeError("A base CAR precisa conter polígonos.")

    _log(callback, "Definindo o sistema de referência métrico…")
    crs_metrico = escolher_crs_metrico(linha_original, config.get("crs_metrico"))
    _log(callback, "CRS métrico utilizado: " + crs_metrico.authid())

    linha = reprojetar(linha_original, crs_metrico)
    car = reprojetar(car_original, crs_metrico)
    linha = reparar_geometrias(linha, "tracado", callback)
    car = reparar_geometrias(car, "CAR", callback)

    modo = config["modo_buffer"].lower()
    if modo == "fixo":
        _log(callback, "Criando buffer fixo de {:.2f} m…".format(config["largura_total_m"]))
        faixa = criar_buffer_fixo(linha, config["largura_total_m"])
    elif modo == "variavel":
        if not config.get("segmentos"):
            raise RuntimeError("Selecione a camada de segmentos para o modo variável.")
        segmentos_original = carregar_vetor(config["segmentos"], "segmentos")
        if QgsWkbTypes.geometryType(segmentos_original.wkbType()) != QgsWkbTypes.LineGeometry:
            raise RuntimeError("A camada de segmentos precisa conter linhas.")
        segmentos = reprojetar(segmentos_original, crs_metrico)
        segmentos = reparar_geometrias(segmentos, "segmentos", callback)
        faixa = criar_buffer_variavel(segmentos, config["campo_largura"])
        _log(callback, "Buffer variável criado.")
    else:
        raise RuntimeError("Modo de buffer inválido.")

    faixa = reparar_geometrias(faixa, "faixa_servidao", callback)
    faixa.setName("faixa_servidao")

    _log(callback, "Identificando imóveis CAR interceptados e calculando métricas…")
    imoveis, intersecoes, areas = construir_camadas_resultado(
        car, faixa, config["campo_id"]
    )
    imoveis.setName("imoveis_atingidos")
    intersecoes.setName("intersecoes")

    area_faixa_ha = sum(
        feature.geometry().area() for feature in faixa.getFeatures()
    ) / 10000.0
    total = imoveis.featureCount()

    gpkg = os.path.join(pasta_saida, "analise_servidao.gpkg")
    xlsx = os.path.join(pasta_saida, "imoveis_atingidos.xlsx")
    pdf = os.path.join(pasta_saida, "relatorio_servidao.pdf")

    _log(callback, "Gravando GeoPackage…")
    if os.path.exists(gpkg):
        try:
            os.remove(gpkg)
        except Exception as exc:
            raise RuntimeError(
                "Não foi possível substituir o GeoPackage existente. "
                "Feche camadas/arquivos que estejam usando analise_servidao.gpkg. "
                + str(exc)
            )
    salvar_gpkg(faixa, gpkg, "faixa_servidao", first=True)
    salvar_gpkg(imoveis, gpkg, "imoveis_atingidos")
    salvar_gpkg(intersecoes, gpkg, "intersecoes")

    _log(callback, "Exportando planilha Excel…")
    exportar_excel(imoveis, xlsx, config["campo_id"], config.get("campo_nome", ""))

    _log(callback, "Montando relatório PDF…")
    fontes = [
        "Base de imóveis do Cadastro Ambiental Rural (CAR)",
        "Traçado fornecido para análise"
    ]
    exportar_relatorio_pdf(
        faixa=faixa,
        imoveis=imoveis,
        intersecoes=intersecoes,
        caminho_pdf=pdf,
        campo_id=config["campo_id"],
        campo_nome=config.get("campo_nome", ""),
        campo_municipio=config.get("campo_municipio", "municipio"),
        modo_buffer=modo,
        largura_total_m=(config.get("largura_total_m") if modo == "fixo" else None),
        fontes=fontes,
        tracado=linha
    )

    project = QgsProject.instance()
    project.addMapLayer(linha)
    project.addMapLayer(faixa)
    project.addMapLayer(imoveis)
    project.addMapLayer(intersecoes)

    _log(callback, "Análise concluída.")
    return {
        "total_imoveis": total,
        "area_faixa_ha": area_faixa_ha,
        "gpkg": gpkg,
        "xlsx": xlsx,
        "pdf": pdf,
        "crs": crs_metrico.authid()
    }
