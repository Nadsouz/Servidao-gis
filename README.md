# Servidão GIS

Plugin QGIS para análise de faixa de servidão e imóveis interceptados pelo Cadastro Ambiental Rural (CAR).

## Compatibilidade inicial
- QGIS 3.28 ou superior (desenvolvido tomando como referência o QGIS 3.44.12 Solothurn).
- Provedor Processing habilitado.
- Biblioteca Python `openpyxl` disponível no ambiente Python utilizado pelo QGIS para exportação XLSX.

## Instalação para testes
1. No QGIS, acesse **Complementos → Gerenciar e Instalar Complementos**.
2. Abra a opção **Instalar a partir de ZIP**.
3. Selecione `servidao_gis_plugin.zip`.
4. Ative o complemento **Servidão GIS**.
5. Abra-o pelo menu **Complementos → Servidão GIS** ou pelo ícone da barra de ferramentas.

## Uso
1. Selecione o arquivo do traçado linear.
2. Selecione a base CAR poligonal.
3. Escolha buffer fixo ou variável.
4. Informe os campos de identificação e, se necessário, o campo de largura.
5. Defina a pasta de saída e execute.

O plugin gera:
- `analise_servidao.gpkg` (faixa, imóveis atingidos e interseções);
- `imoveis_atingidos.xlsx`;
- `relatorio_servidao.pdf`.

## Imagens de uso
- interface [Interface do Servidão GIS](docs/Imagens/interface.jpeg)

## Observações importantes
- A largura informada é total. Exemplo: 60 m significa 30 m para cada lado do eixo.
- No modo variável, cada segmento precisa possuir um valor positivo no campo de largura.
- O CRS métrico é estimado pela posição do traçado quando o campo CRS fica vazio. Para áreas extensas ou casos que exijam uma projeção específica, informe o EPSG adequado.
- O módulo PDF foi mantido a partir do arquivo `pdf_export.py` fornecido para preservar o relatório que já estava funcionando.
- Esta é uma primeira versão de teste. Antes de distribuir ou publicar no repositório oficial do QGIS, é necessário validar o funcionamento no ambiente QGIS e revisar os metadados/licença.

## Estrutura
```text
servidao_gis/
├── __init__.py
├── metadata.txt
├── servidao_gis.py
├── dialog.py
├── core/
│   └── processamento.py
└── exports/
    ├── excel_export.py
    └── pdf_export.py
```
