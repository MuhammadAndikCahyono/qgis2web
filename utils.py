# qgis-ol3 Creates OpenLayers map from QGIS layers
# Copyright (C) 2014 Victor Olaya (volayaf@gmail.com)
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import os
import re
import time
from PyQt4.QtCore import *
from qgis.core import *
import processing
from subprocess import *
import traceback
import tempfile

NO_POPUP = 0
ALL_ATTRIBUTES = 1

TYPE_MAP = {
    QGis.WKBPoint: 'Point',
    QGis.WKBLineString: 'LineString',
    QGis.WKBPolygon: 'Polygon',
    QGis.WKBPolygon25D: 'Polygon',
    QGis.WKBMultiPoint: 'MultiPoint',
    QGis.WKBMultiLineString: 'MultiLineString',
    QGis.WKBMultiPolygon: 'MultiPolygon',
    QGis.WKBMultiPolygon25D: 'MultiPolygon'}


def tempFolder():
    tempDir = os.path.join(unicode(QDir.tempPath()), 'qgis2web')
    if not QDir(tempDir).exists():
        QDir().mkpath(tempDir)

    return unicode(os.path.abspath(tempDir))


def getUsedFields(layer):
    fields = []
    try:
        fields.append(layer.rendererV2().classAttribute())
    except:
        pass
    labelsEnabled = unicode(
        layer.customProperty("labeling/enabled")).lower() == "true"
    if labelsEnabled:
        fields.append(layer.customProperty("labeling/fieldName"))
    return fields


def writeTmpLayer(layer, popup):
    usedFields = getUsedFields(layer)
    if popup != ALL_ATTRIBUTES:
        uri = TYPE_MAP[layer.wkbType()]
        crs = layer.crs()
        if crs.isValid():
            uri += '?crs=' + crs.authid()
        if popup != NO_POPUP:
            usedFields.append(popup)
        for field in usedFields:
            fieldType = layer.pendingFields().field(field).type()
            fieldType = "double" if (fieldType == QVariant.Double or
                                     fieldType == QVariant.Int) else (
                                        "string")
            uri += '&field=' + unicode(field) + ":" + fieldType
        newlayer = QgsVectorLayer(uri, layer.name(), 'memory')
        writer = newlayer.dataProvider()
        outFeat = QgsFeature()
        for feature in layer.getFeatures():
            outFeat.setGeometry(feature.geometry())
            attrs = [feature[f] for f in usedFields]
            if attrs:
                outFeat.setAttributes(attrs)
            writer.addFeatures([outFeat])
        layer = newlayer
    return layer


def exportLayers(iface, layers, folder, precision, optimize, popupField, json):
    srcCrs = iface.mapCanvas().mapSettings().destinationCrs()
    epsg4326 = QgsCoordinateReferenceSystem("EPSG:4326")
    layersFolder = os.path.join(folder, "layers")
    QDir().mkpath(layersFolder)
    reducePrecision = (
        re.compile(r"([0-9]+\.[0-9]{%s})([0-9]+)" % unicode(int(precision))))
    for layer, encode2json, popup in zip(layers, json, popupField):
        if (layer.type() == layer.VectorLayer and
                (layer.providerType() != "WFS" or encode2json)):
            cleanLayer = writeTmpLayer(layer, popup)
            try:
                if layer.rendererV2().type() == "25dRenderer":
                    # print safeLayerName + " is 2.5d"
                    provider = cleanLayer.dataProvider()
                    provider.addAttributes([QgsField("height",
                                                     QVariant.Double),
                                            QgsField("wallColor",
                                                     QVariant.String),
                                            QgsField("roofColor",
                                                     QVariant.String)])
                    cleanLayer.updateFields()
                    fields = cleanLayer.pendingFields()
                    feats = layer.getFeatures()
                    context = QgsExpressionContext()
                    context.appendScope(
                                QgsExpressionContextUtils.layerScope(layer))
                    expression = QgsExpression('eval(@qgis_25d_height)')
                    for feat in feats:
                        context.setFeature(feat)
                        height = expression.evaluate(context)
                        symbol = layer.rendererV2().symbolForFeature(feat)
                        wallLayer = symbol.symbolLayer(1)
                        roofLayer = symbol.symbolLayer(2)
                        wallColor = wallLayer.subSymbol().color().name()
                        roofColor = roofLayer.subSymbol().color().name()
                        try:
                            height = height * 5
                        except:
                            pass
                        heightField = fields.indexFromName("height")
                        wallField = fields.indexFromName("wallColor")
                        roofField = fields.indexFromName("roofColor")
                        provider.changeAttributeValues(
                                {feat.id():
                                 {heightField: height,
                                  wallField: wallColor,
                                  roofField: roofColor}})
            except:
                print traceback.format_exc()

            tmpPath = os.path.join(layersFolder,
                                   safeName(cleanLayer.name()) + ".json")
            path = os.path.join(layersFolder,
                                safeName(cleanLayer.name()) + ".js")
            QgsVectorFileWriter.writeAsVectorFormat(cleanLayer, tmpPath,
                                                    "utf-8", epsg4326,
                                                    'GeoJson')
            with open(path, "w") as f:
                f.write("var %s = " % ("geojson_" +
                                       safeName(cleanLayer.name())))
                with open(tmpPath, "r") as f2:
                    for line in f2:
                        line = reducePrecision.sub(r"\1", line)
                        if optimize:
                            line = line.strip("\n\t ")
                            line = removeSpaces(line)
                        f.write(line)
            os.remove(tmpPath)
        elif layer.type() == layer.RasterLayer:
            name_ts = safeName(layer.name()) + unicode(time.time())
            in_raster = unicode(layer.dataProvider().dataSourceUri())
            prov_raster = os.path.join(tempfile.gettempdir(),
                                       'json_' + name_ts + '_prov.tif')
            out_raster = os.path.join(layersFolder,
                                      safeName(layer.name()) + ".png")
            crsSrc = layer.crs()
            crsDest = QgsCoordinateReferenceSystem(3857)
            xform = QgsCoordinateTransform(crsSrc, crsDest)
            extentRep = xform.transform(layer.extent())
            extentRepNew = ','.join([unicode(extentRep.xMinimum()),
                                     unicode(extentRep.xMaximum()),
                                     unicode(extentRep.yMinimum()),
                                     unicode(extentRep.yMaximum())])
            processing.runalg("gdalogr:warpreproject", in_raster,
                              layer.crs().authid(), "EPSG:3857", "", 0, 1,
                              0, -1, 75, 6, 1, False, 0, False, "",
                              prov_raster)
            processing.runalg("gdalogr:translate", prov_raster, 100,
                              True, "", 0, "", extentRepNew, False, 0,
                              0, 75, 6, 1, False, 0, False, "",
                              out_raster)


def is25d(layer):
    print "is25d() called"
    renderer = layer.rendererV2()
    symbols = []
    if isinstance(renderer, QgsCategorizedSymbolRendererV2):
        categories = renderer.categories()
        for category in categories:
            symbols.append(category.symbol())
    elif isinstance(renderer, QgsGraduatedSymbolRendererV2):
        ranges = renderer.ranges()
        for range in ranges:
            symbols.append(range.symbol())
    else:
        features = layer.getFeatures()
        for feature in features:
            symbol = renderer.symbolForFeature(feature)
            symbols.append(symbol)

    for sym in symbols:
        try:
            sl0 = sym.symbolLayer(0)
            sl1 = sym.symbolLayer(1)
            sl2 = sym.symbolLayer(2)
            print "geomgen: " + unicode(isinstance(sl1, QgsGeometryGeneratorSymbolLayerV2))
            if (sl0.paintEffect().effectList()[0].enabled() and
                    isinstance(sl1, QgsGeometryGeneratorSymbolLayerV2) and
                    isinstance(sl2, QgsGeometryGeneratorSymbolLayerV2)):
                print layer.name() + " is 2.5d"
                return True
        except:
            print traceback.format_exc()
    print layer.name() + " is NOT 2.5d"
    return False


def safeName(name):
    # TODO: we are assuming that at least one character is valid...
    validChr = '123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
    return ''.join(c for c in name if c in validChr)


def removeSpaces(txt):
    return '"'.join(it if i % 2 else ''.join(it.split())
                    for i, it in enumerate(txt.split('"')))


def scaleToZoom(scale):
    if scale < 1000:
        return 19
    elif scale < 2000:
        return 18
    elif scale < 4000:
        return 17
    elif scale < 8000:
        return 16
    elif scale < 15000:
        return 15
    elif scale < 35000:
        return 14
    elif scale < 70000:
        return 13
    elif scale < 150000:
        return 12
    elif scale < 250000:
        return 11
    elif scale < 500000:
        return 10
    elif scale < 1000000:
        return 9
    elif scale < 2000000:
        return 8
    elif scale < 4000000:
        return 7
    elif scale < 10000000:
        return 6
    elif scale < 15000000:
        return 5
    elif scale < 35000000:
        return 4
    elif scale < 70000000:
        return 3
    elif scale < 150000000:
        return 2
    elif scale < 250000000:
        return 1
    else:
        return 0


def replaceInTemplate(template, values):
    path = os.path.join(os.path.dirname(__file__), "templates", template)
    with open(path) as f:
        lines = f.readlines()
    s = "".join(lines)
    for name, value in values.iteritems():
        s = s.replace(name, value)
    return s
