# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QDoubleSpinBox, QTextEdit,
    QMessageBox, QGroupBox, QGridLayout
)
from qgis.PyQt.QtGui import QIcon
from .core.processamento import executar_analise


class ServidaoGISDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface

        self.setWindowTitle("Servidão GIS — Análise de faixa de servidão")

        icon_path = os.path.join(
            os.path.dirname(__file__),
            'resources',
            'icon.svg'
        )
        self.setWindowIcon(QIcon(icon_path))

        self.setMinimumWidth(680)
        self.setMinimumHeight(650)

        self._montar_interface()
    def _montar_interface(self):
        root = QVBoxLayout(self)

        intro = QLabel(
            "Informe o traçado, a base CAR e os parâmetros da análise. "
            "Os arquivos de saída serão gravados na pasta indicada."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        root.addLayout(form)

        self.ed_tracado = self._campo_arquivo(form, "Traçado (KML/GPKG/SHP):", "vetor")
        self.ed_car = self._campo_arquivo(form, "Base de imóveis CAR:", "vetor")
        self.ed_segmentos = self._campo_arquivo(form, "Segmentos (modo variável):", "vetor")

        self.combo_modo = QComboBox()
        self.combo_modo.addItem("Largura fixa", "fixo")
        self.combo_modo.addItem("Largura variável por segmento", "variavel")
        self.combo_modo.currentIndexChanged.connect(self._atualizar_modo)
        form.addRow("Modo do buffer:", self.combo_modo)

        self.spin_largura = QDoubleSpinBox()
        self.spin_largura.setRange(0.01, 1000000)
        self.spin_largura.setDecimals(2)
        self.spin_largura.setValue(60.0)
        self.spin_largura.setSuffix(" m")
        form.addRow("Largura total (modo fixo):", self.spin_largura)

        self.ed_campo_largura = QLineEdit("largura_m")
        form.addRow("Campo de largura (variável):", self.ed_campo_largura)

        self.ed_campo_id = QLineEdit("cod_imovel")
        form.addRow("Campo identificador CAR:", self.ed_campo_id)

        self.ed_campo_nome = QLineEdit()
        self.ed_campo_nome.setPlaceholderText("Opcional — ex.: nom_imovel")
        form.addRow("Campo nome do imóvel:", self.ed_campo_nome)

        self.ed_campo_municipio = QLineEdit("municipio")
        form.addRow("Campo município:", self.ed_campo_municipio)

        self.ed_camada_car = QLineEdit()
        self.ed_camada_car.setPlaceholderText("Opcional — nome da camada dentro do arquivo")
        form.addRow("Nome da camada CAR:", self.ed_camada_car)

        self.ed_crs = QLineEdit()
        self.ed_crs.setPlaceholderText("Automático — ou informe, ex.: EPSG:31983")
        form.addRow("CRS métrico:", self.ed_crs)

        self.ed_saida = self._campo_pasta(form, "Pasta de saída:")

        self._atualizar_modo()

        box = QGroupBox("Execução")
        box_layout = QVBoxLayout(box)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("As etapas do processamento aparecerão aqui.")
        box_layout.addWidget(self.log)
        root.addWidget(box, 1)

        buttons = QHBoxLayout()
        self.btn_executar = QPushButton("Executar análise")
        self.btn_executar.clicked.connect(self._executar)
        self.btn_fechar = QPushButton("Fechar")
        self.btn_fechar.clicked.connect(self.close)
        buttons.addWidget(self.btn_executar)
        buttons.addStretch()
        buttons.addWidget(self.btn_fechar)
        root.addLayout(buttons)

    def _campo_arquivo(self, form, label, kind):
        container = QHBoxLayout()
        edit = QLineEdit()
        button = QPushButton("Selecionar…")
        button.clicked.connect(lambda: self._selecionar_arquivo(edit, kind))
        container.addWidget(edit, 1)
        container.addWidget(button)
        form.addRow(label, container)
        return edit

    def _campo_pasta(self, form, label):
        container = QHBoxLayout()
        edit = QLineEdit()
        button = QPushButton("Selecionar…")
        button.clicked.connect(lambda: self._selecionar_pasta(edit))
        container.addWidget(edit, 1)
        container.addWidget(button)
        form.addRow(label, container)
        return edit

    def _selecionar_arquivo(self, edit, kind):
        if kind == "vetor":
            path, _ = QFileDialog.getOpenFileName(
                self, "Selecionar camada vetorial", "",
                "Dados vetoriais (*.kml *.kmz *.gpkg *.shp *.zip *.geojson);;Todos os arquivos (*.*)"
            )
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Selecionar arquivo", "")
        if path:
            edit.setText(path)

    def _selecionar_pasta(self, edit):
        path = QFileDialog.getExistingDirectory(self, "Selecionar pasta de saída")
        if path:
            edit.setText(path)

    def _atualizar_modo(self):
        variavel = self.combo_modo.currentData() == "variavel"
        self.ed_segmentos.setEnabled(variavel)
        self.ed_campo_largura.setEnabled(variavel)
        self.spin_largura.setEnabled(not variavel)

    def _log(self, mensagem):
        self.log.append(str(mensagem))
        self.log.ensureCursorVisible()

    def _executar(self):
        caminho_tracado = self.ed_tracado.text().strip()
        caminho_car = self.ed_car.text().strip()
        pasta_saida = self.ed_saida.text().strip()
        campo_id = self.ed_campo_id.text().strip()

        obrigatorios = [
            (caminho_tracado, "Selecione o arquivo do traçado."),
            (caminho_car, "Selecione a base de imóveis CAR."),
            (pasta_saida, "Selecione a pasta de saída."),
            (campo_id, "Informe o campo identificador do CAR.")
        ]
        for valor, mensagem in obrigatorios:
            if not valor:
                QMessageBox.warning(self, "Dados incompletos", mensagem)
                return

        if not os.path.isfile(caminho_tracado):
            QMessageBox.warning(self, "Arquivo não encontrado", "O arquivo do traçado não foi encontrado.")
            return
        if not os.path.exists(caminho_car):
            QMessageBox.warning(self, "Arquivo não encontrado", "A base CAR não foi encontrada.")
            return

        modo = self.combo_modo.currentData()
        if modo == "variavel" and not self.ed_segmentos.text().strip():
            QMessageBox.warning(self, "Dados incompletos", "Selecione a camada de segmentos para o modo variável.")
            return

        config = {
            "tracado": caminho_tracado,
            "car": caminho_car,
            "car_camada": self.ed_camada_car.text().strip(),
            "segmentos": self.ed_segmentos.text().strip(),
            "modo_buffer": modo,
            "largura_total_m": float(self.spin_largura.value()),
            "campo_largura": self.ed_campo_largura.text().strip(),
            "campo_id": campo_id,
            "campo_nome": self.ed_campo_nome.text().strip(),
            "campo_municipio": self.ed_campo_municipio.text().strip() or "municipio",
            "crs_metrico": self.ed_crs.text().strip() or None,
            "pasta_saida": pasta_saida
        }

        self.btn_executar.setEnabled(False)
        self.log.clear()
        self._log("Iniciando análise…")
        try:
            resultado = executar_analise(config, self._log)
            self._log("")
            self._log("PROCESSAMENTO CONCLUÍDO")
            self._log("Imóveis identificados: {}".format(resultado["total_imoveis"]))
            self._log("Área geométrica da faixa: {:.3f} ha".format(resultado["area_faixa_ha"]))
            self._log("GeoPackage: " + resultado["gpkg"])
            self._log("Excel: " + resultado["xlsx"])
            self._log("PDF: " + resultado["pdf"])
            QMessageBox.information(
                self, "Análise concluída",
                "A análise foi concluída. Os arquivos foram salvos na pasta de saída."
            )
        except Exception as exc:
            self._log("ERRO: " + str(exc))
            import traceback
            self._log(traceback.format_exc())
            QMessageBox.critical(self, "Erro na análise", str(exc))
        finally:
            self.btn_executar.setEnabled(True)
