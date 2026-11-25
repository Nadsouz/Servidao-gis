# -*- coding: utf-8 -*-
def classFactory(iface):
    from .servidao_gis import ServidaoGIS
    return ServidaoGIS(iface)
