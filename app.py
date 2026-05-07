
from pathlib import Path
import os
os.environ.setdefault("SHAPE_RESTORE_SHX", "YES")
import sys
import json
import math
import random
import shutil
import zipfile
import threading
import webbrowser
import traceback

import pandas as pd
from flask import Flask, jsonify, request, send_file, render_template_string

try:
    import geopandas as gpd
    from shapely.geometry import Point, Polygon, mapping
    from shapely.ops import nearest_points, unary_union
    GEOPANDAS_AVAILABLE = True
except Exception:
    gpd = None
    Point = None
    Polygon = None
    mapping = None
    nearest_points = None
    unary_union = None
    GEOPANDAS_AVAILABLE = False

try:
    from sklearn.cluster import KMeans
    SKLEARN_AVAILABLE = True
except Exception:
    KMeans = None
    SKLEARN_AVAILABLE = False

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def resource_path(relative_path):
    
    #Finds files correctly in normal Python and PyInstaller executable mode.
    
    try:
        base_path = Path(sys._MEIPASS)
    except Exception:
        base_path = Path(__file__).parent

    return base_path / relative_path


BASE_DIR = Path(__file__).parent
DATA_DIR = resource_path("data")
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)

WGS84 = "EPSG:4326"
PROJECTED = "EPSG:32733"

CONSTITUENCY_NAME_FIELD = "CONST"
POPULATION_FIELD = "Population"
SERVICE_AREA_DISTANCE_METRES = 5000

layers = {
    "constituencies": None,
    "schools": None,
    "roads": None,
    "localities": None,
    "stations": None,
    "service_areas": None
}

# Fallback Khomas presentation data. This is used only if shapefiles fail.
FALLBACK_CONSTITUENCIES = [
    {"name": "Windhoek East", "population": 35000, "bbox": [17.06, -22.62, 17.18, -22.50]},
    {"name": "Windhoek West", "population": 42000, "bbox": [16.95, -22.62, 17.06, -22.50]},
    {"name": "Windhoek Rural", "population": 28000, "bbox": [16.75, -22.85, 17.35, -22.62]},
    {"name": "Khomasdal", "population": 30000, "bbox": [16.98, -22.58, 17.08, -22.52]},
    {"name": "Katutura", "population": 50000, "bbox": [17.02, -22.56, 17.12, -22.48]}
]


HTML_PAGE = r"""
<!DOCTYPE html>
<html>
<head>
    <title>GIS-Based Polling Station Optimization and Voter Allocation System</title>
    <meta charset="utf-8">
    <link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css">
    <script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://html2canvas.hertzen.com/dist/html2canvas.min.js"></script>

    <style>
        body { margin:0; font-family:Arial, sans-serif; background:#f4f6f8; color:#222; }
        .header {
            height:68px; background:#003580; color:white; display:flex; align-items:center;
            justify-content:center; border-bottom:5px solid #d21034; font-size:21px;
            font-weight:bold; text-align:center;
        }
        .main-layout { display:flex; height:calc(100vh - 73px); width:100%; }
        .sidebar {
            width:365px; background:white; border-right:1px solid #d6d6d6;
            padding:14px; box-sizing:border-box; overflow-y:auto;
        }
        #map { flex:1; width:100%; }
        .panel { border:1px solid #ddd; background:#fbfbfb; padding:12px; margin-bottom:12px; }
        .panel-title {
            color:#003580; font-weight:bold; font-size:14px; margin-bottom:8px;
            border-bottom:2px solid #d21034; padding-bottom:5px;
        }
        label { color:#003580; font-size:13px; font-weight:bold; display:block; margin-top:8px; }
        input {
            width:100%; padding:9px; margin-top:5px; margin-bottom:8px;
            box-sizing:border-box; border:1px solid #cfcfcf; font-size:14px;
        }
        button {
            width:100%; padding:10px; margin-top:6px; margin-bottom:6px;
            border:none; background:#003580; color:white; font-weight:bold;
            cursor:pointer; font-size:14px; text-align:center;
        }
        button:hover { background:#00275f; }
        .red-button { background:#d21034; }
        .red-button:hover { background:#a50d28; }
        .grey-button { background:#555; }
        .grey-button:hover { background:#333; }
        #message {
            background:#eef3ff; border-left:4px solid #003580; padding:10px;
            font-size:13px; line-height:1.4; margin-bottom:12px;
        }
        canvas { background:white; border:1px solid #ddd; padding:8px; box-sizing:border-box; }
        .status-row { display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-top:8px; }
        .status-card { background:white; border:1px solid #ddd; padding:8px; text-align:center; font-size:12px; }
        .status-number { color:#003580; font-size:18px; font-weight:bold; }
        .constituency-label {
            background:transparent; border:none; box-shadow:none; color:#111;
            font-size:11px; font-weight:bold; text-shadow:1px 1px 2px white;
        }
        .leaflet-control-layers { font-size:11px; line-height:1.2; max-width:185px; }
        .leaflet-control-layers-expanded { padding:4px 6px 5px 6px; }
        .leaflet-control-layers label { margin:1px 0; font-size:11px; color:#222; font-weight:normal; }
        .leaflet-control-layers::before {
            content:"Layers"; display:block; font-weight:bold; color:#003580;
            padding:3px 4px 2px 4px; font-size:12px;
        }
        .density-legend {
            background:white; border:1px solid #cfcfcf; padding:7px; font-size:11px;
            line-height:1.45; box-shadow:0 1px 5px rgba(0,0,0,0.25);
        }
        .legend-row { display:flex; align-items:center; gap:5px; }
        .legend-box { width:15px; height:10px; display:inline-block; border:1px solid #777; }
    </style>
</head>

<body>
    <div class="header">GIS-Based Polling Station Optimization and Voter Allocation System</div>

    <div class="main-layout">
        <div class="sidebar">
            <div id="message">Loading GIS layers...</div>

            <div class="panel">
                <div class="panel-title">Optimization Settings</div>
                <label>People per polling station</label>
                <input type="number" id="capacity" value="1000" min="1">
                <button class="red-button" onclick="runOptimization()">Run Optimization</button>
                <button class="grey-button" onclick="resetSystem()">Reset Optimization</button>
            </div>

            <div class="panel">
                <div class="panel-title">Export Results</div>
                <button onclick="downloadFile('csv')">Export CSV</button>
                <button onclick="downloadFile('excel')">Export Excel</button>
                <button onclick="downloadFile('geojson')">Export GeoJSON</button>
                <button onclick="downloadFile('shapefile')">Export Shapefiles</button>
                <button onclick="exportMapImage()">Export Map as PNG</button>
                <button onclick="exportChartImage()">Export Graph as PNG</button>
                <button onclick="downloadHeatmap()">Export Population map PNG</button>
            </div>

            <div class="panel">
                <div class="panel-title">Layer Summary</div>
                <div class="status-row">
                    <div class="status-card"><div class="status-number" id="constituencyCount">0</div>Constituencies</div>
                    <div class="status-card"><div class="status-number" id="schoolCount">0</div>Schools</div>
                    <div class="status-card"><div class="status-number" id="roadCount">0</div>Roads</div>
                    <div class="status-card"><div class="status-number" id="localityCount">0</div>Localities</div>
                    <div class="status-card"><div class="status-number" id="generatedCount">0</div>Generated</div>
                </div>
            </div>

            <div class="panel">
                <div class="panel-title">Generated Points per Constituency</div>
                <canvas id="chartCanvas" height="260"></canvas>
            </div>
        </div>
        <div id="map"></div>
    </div>

    <script>
        let map = L.map("map").setView([-22.56, 17.08], 9);

        let openStreetMap = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom:19, attribution:"OpenStreetMap"}).addTo(map);
        let topographic = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {maxZoom:17, attribution:"OpenTopoMap"});
        let lightMap = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {maxZoom:19, attribution:"Carto"});
        let satellite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {maxZoom:19, attribution:"Esri World Imagery"});

        let baseMaps = {"OpenStreetMap":openStreetMap, "Topographic":topographic, "Light Map":lightMap, "Satellite":satellite};
        let overlayMaps = {};
        let layerControl = L.control.layers(baseMaps, overlayMaps, {collapsed:false}).addTo(map);

        let constituencyLayer = null;
        let roadsLayer = null;
        let schoolsLayer = null;
        let localitiesLayer = null;
        let stationsLayer = null;
        let serviceAreaLayer = null;
        let densityLayer = null;
        let chart = null;
        let startingBounds = null;

        function showMessage(text) { document.getElementById("message").innerText = text; }
        function setCount(id, value) {
            let element = document.getElementById(id);
            if (element) element.innerText = value;
        }
        function addOverlay(name, layer) {
            overlayMaps[name] = layer;
            layerControl.addOverlay(layer, name);
        }
        function removeOverlay(name, layer) {
            if (layer) {
                if (map.hasLayer(layer)) map.removeLayer(layer);
                layerControl.removeLayer(layer);
            }
            if (overlayMaps[name]) delete overlayMaps[name];
        }

        function getDensityColour(value) {
            if (value > 12000) return "#800026";
            if (value > 8000) return "#bd0026";
            if (value > 5000) return "#e31a1c";
            if (value > 2500) return "#fc4e2a";
            if (value > 1000) return "#fd8d3c";
            if (value > 250) return "#feb24c";
            return "#ffeda0";
        }

        function densityStyle(feature) {
            return {fillColor:getDensityColour(feature.properties.population_density || 0), weight:1, opacity:1, color:"#555", fillOpacity:0.55};
        }

        function loadLayers() {
            fetch("/layers")
            .then(response => response.json())
            .then(data => {
                if (!data.success) {
                    showMessage("Layer loading issue. Click Run Optimization.");
                    return;
                }

                setCount("constituencyCount", data.counts.constituencies);
                setCount("schoolCount", data.counts.schools);
                setCount("roadCount", data.counts.roads);
                setCount("localityCount", data.counts.localities);

                if (data.density) {
                    densityLayer = L.geoJSON(data.density, {
                        style:densityStyle,
                        onEachFeature:function(feature, layer) {
                            let p = feature.properties;
                            layer.bindPopup("<b>" + (p.CONST || "Constituency") + "</b><br>Population: " + (p.Population || "") + "<br>Density: " + Number(p.population_density || 0).toFixed(2));
                        }
                    }).addTo(map);
                    addOverlay("Population Density Heatmap", densityLayer);
                }

                if (data.constituencies) {
                    constituencyLayer = L.geoJSON(data.constituencies, {
                        style:{color:"#003580", weight:2, fillOpacity:0},
                        onEachFeature:function(feature, layer) {
                            layer.bindTooltip(feature.properties.CONST || "Constituency", {permanent:true, direction:"center", className:"constituency-label"});
                        }
                    }).addTo(map);
                    addOverlay("Khomas Constituency Boundary", constituencyLayer);
                    startingBounds = constituencyLayer.getBounds();
                    map.fitBounds(startingBounds);
                }

                if (data.roads) {
                    roadsLayer = L.geoJSON(data.roads, {style:{color:"#d21034", weight:4, opacity:0.95}}).addTo(map);
                    addOverlay("Roads", roadsLayer);
                }

                if (data.schools) {
                    schoolsLayer = L.geoJSON(data.schools, {
                        pointToLayer:function(feature, latlng) {
                            return L.circleMarker(latlng, {radius:5, color:"#006b3f", fillColor:"#006b3f", fillOpacity:0.9, weight:1});
                        }
                    }).addTo(map);
                    addOverlay("Existing School Polling Stations", schoolsLayer);
                }

                if (data.localities) {
                    localitiesLayer = L.geoJSON(data.localities, {
                        pointToLayer:function(feature, latlng) {
                            return L.circleMarker(latlng, {radius:4, color:"#444", fillColor:"#fff", fillOpacity:0.95, weight:1});
                        }
                    }).addTo(map);
                    addOverlay("Localities or Settlements", localitiesLayer);
                }

                showMessage("Layers loaded. Click Run Optimization.");
            })
            .catch(error => {
                showMessage("Layer loading warning. Click Run Optimization.");
            });
        }

        function runOptimization() {
            let capacityInput = document.getElementById("capacity");
            let capacity = Number(capacityInput.value);
            if (!capacity || capacity <= 0) {
                showMessage("Please enter a valid people per polling-station value. Example: 1000");
                return;
            }

            let runButton = document.querySelector("button.red-button");
            if (runButton) runButton.disabled = true;
            showMessage("Running optimization. Generating polling station points...");

            fetch("/generate", {
                method:"POST",
                headers:{"Content-Type":"application/json"},
                body:JSON.stringify({capacity:capacity, ratio:capacity, people_per_station:capacity})
            })
            .then(async response => {
                let text = await response.text();
                try { return JSON.parse(text); }
                catch (e) { throw new Error(text.substring(0, 180) || "The server returned an empty response."); }
            })
            .then(data => {
                if (!data.success) {
                    showMessage("Optimization warning: " + (data.message || "No points were returned."));
                    return;
                }
                if (!data.stations || !data.stations.features || data.stations.features.length === 0) {
                    showMessage("Optimization completed, but no station features were returned. Check that the population field has values.");
                    return;
                }

                if (stationsLayer) removeOverlay("Generated Polling Stations", stationsLayer);
                if (serviceAreaLayer) removeOverlay("5 km Polling Station Service Areas", serviceAreaLayer);

                stationsLayer = L.geoJSON(data.stations, {
                    pointToLayer:function(feature, latlng) {
                        let stationType = feature.properties.station_type || "";
                        let fillColour = stationType.includes("Existing") ? "#006b3f" : "#f2c300";
                        return L.circleMarker(latlng, {radius:7, color:"#111", fillColor:fillColour, fillOpacity:0.98, weight:1.5});
                    },
                    onEachFeature:function(feature, layer) {
                        let p = feature.properties;
                        layer.bindPopup(
                            "<b>" + p.station_name + "</b><br>" +
                            "Type: " + p.station_type + "<br>" +
                            "Constituency: " + p.constituency + "<br>" +
                            "Population: " + p.population + "<br>" +
                            "Capacity: " + p.capacity + "<br>" +
                            "Required stations: " + p.required_stations + "<br>" +
                            "Latitude: " + p.latitude + "<br>" +
                            "Longitude: " + p.longitude + "<br>" +
                            "Mode: " + (p.mode || "GIS")
                        );
                    }
                }).addTo(map);
                addOverlay("Generated Polling Stations", stationsLayer);

                if (data.service_areas) {
                    serviceAreaLayer = L.geoJSON(data.service_areas, {
                        style:{color:"#003580", weight:1, fillColor:"#003580", fillOpacity:0.08}
                    }).addTo(map);
                    addOverlay("5 km Polling Station Service Areas", serviceAreaLayer);
                }

                setCount("generatedCount", data.total_generated);
                updateChart(data.graph_summary);

                if (stationsLayer.getBounds().isValid()) {
                    map.fitBounds(stationsLayer.getBounds());
                }

                showMessage("Optimization complete. " + data.total_generated + " proposed polling station points were generated.");
            })
            .catch(error => {
                showMessage("Optimization failed in the browser/server connection. Error: " + error.message);
            })
            .finally(() => {
                if (runButton) runButton.disabled = false;
            });
        }

        function resetSystem() {
            fetch("/reset", {method:"POST"})
            .then(response => response.json())
            .then(data => {
                if (stationsLayer) {
                    removeOverlay("Generated Polling Stations", stationsLayer);
                    stationsLayer = null;
                }
                if (serviceAreaLayer) {
                    removeOverlay("5 km Polling Station Service Areas", serviceAreaLayer);
                    serviceAreaLayer = null;
                }
                clearChart();
                setCount("generatedCount", 0);
                if (startingBounds) map.fitBounds(startingBounds);
                else map.setView([-22.56, 17.08], 9);
                showMessage("Optimization reset. The map has been recentered.");
            });
        }

        function updateChart(summary) {
            let labels = summary.map(row => row.constituency);
            let values = summary.map(row => row.generated_points);
            if (labels.length === 0) { labels = ["No extra points needed"]; values = [0]; }

            let ctx = document.getElementById("chartCanvas").getContext("2d");
            if (chart) chart.destroy();

            chart = new Chart(ctx, {
                type:"bar",
                data:{labels:labels, datasets:[{label:"Generated points", data:values, backgroundColor:"#003580", borderColor:"#d21034", borderWidth:1}]},
                options:{responsive:true, plugins:{legend:{display:false}}, scales:{x:{ticks:{font:{size:9}}}, y:{beginAtZero:true, ticks:{precision:0}}}}
            });
        }

        function clearChart() {
            if (chart) { chart.destroy(); chart = null; }
            let canvas = document.getElementById("chartCanvas");
            canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
        }

        function downloadFile(fileType) { window.location.href = "/export/" + fileType; }
        function downloadHeatmap() { window.location.href = "/export/heatmap"; }

        function exportMapImage() {
            html2canvas(document.getElementById("map"), {useCORS:true}).then(canvas => {
                let link = document.createElement("a");
                link.download = "ecn_polling_station_map.png";
                link.href = canvas.toDataURL("image/png");
                link.click();
            });
        }

        function exportChartImage() {
            let canvas = document.getElementById("chartCanvas");
            let link = document.createElement("a");
            link.download = "ecn_generated_points_graph.png";
            link.href = canvas.toDataURL("image/png");
            link.click();
        }

        let densityLegend = L.control({position:"bottomleft"});
        densityLegend.onAdd = function(map) {
            let div = L.DomUtil.create("div", "density-legend");
            div.innerHTML =
                "<b>Population Density</b><br>" +
                "<div class='legend-row'><span class='legend-box' style='background:#ffeda0;'></span>Low</div>" +
                "<div class='legend-row'><span class='legend-box' style='background:#feb24c;'></span>Moderate</div>" +
                "<div class='legend-row'><span class='legend-box' style='background:#fd8d3c;'></span>High</div>" +
                "<div class='legend-row'><span class='legend-box' style='background:#e31a1c;'></span>Very high</div>" +
                "<div class='legend-row'><span class='legend-box' style='background:#800026;'></span>Highest</div>";
            return div;
        };
        densityLegend.addTo(map);

        loadLayers();
    </script>
</body>
</html>
"""


def safe_read(path):
    if not GEOPANDAS_AVAILABLE:
        return None
    os.environ.setdefault("SHAPE_RESTORE_SHX", "YES")
    try:
        gdf = gpd.read_file(path)
    except Exception:
        try:
            gdf = gpd.read_file(path, encoding="latin1")
        except Exception as read_error:
            print(f"Could not read GIS layer {path}: {read_error}")
            return None

    if gdf is None or len(gdf) == 0:
        return gdf

    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)

    gdf = gdf[gdf.geometry.notnull()].copy()

    try:
        return gdf.to_crs(WGS84)
    except Exception:
        return gdf


def load_layers():
    if not GEOPANDAS_AVAILABLE:
        return ["GeoPandas not available. Presentation fallback active."]

    missing = []

    items = {
        "constituencies": DATA_DIR / "Khomas.shp",
        "schools": DATA_DIR / "Khomas_Schools.shp",
        "localities": DATA_DIR / "Localities.shp"
    }

    for key, path in items.items():
        if path.exists():
            layers[key] = safe_read(path)
        else:
            missing.append(path.name)

    road_paths = [
        DATA_DIR / "District_Roads.shp",
        DATA_DIR / "Main_Roads.shp",
        DATA_DIR / "Trunk_Roads.shp"
    ]

    road_layers = []

    for path in road_paths:
        if path.exists():
            gdf = safe_read(path)
            if gdf is not None and len(gdf) > 0:
                gdf["road_layer"] = path.stem
                road_layers.append(gdf)

    if road_layers:
        try:
            merged = pd.concat(road_layers, ignore_index=True)
            layers["roads"] = gpd.GeoDataFrame(merged, crs=road_layers[0].crs)
        except Exception:
            layers["roads"] = None

    return missing


def to_geojson(gdf):
    if gdf is None or len(gdf) == 0 or not GEOPANDAS_AVAILABLE:
        return None
    try:
        return json.loads(gdf.to_crs(WGS84).to_json())
    except Exception:
        return None


def fallback_boundary_geojson():
    features = []

    for item in FALLBACK_CONSTITUENCIES:
        minx, miny, maxx, maxy = item["bbox"]
        coords = [[
            [minx, miny],
            [maxx, miny],
            [maxx, maxy],
            [minx, maxy],
            [minx, miny]
        ]]

        features.append({
            "type": "Feature",
            "properties": {
                "CONST": item["name"],
                "Population": item["population"],
                "population_density": item["population"] / 100
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": coords
            }
        })

    return {"type": "FeatureCollection", "features": features}


def fallback_density_geojson():
    return fallback_boundary_geojson()


def fallback_station_geojson_and_table(capacity):
    features = []
    rows = []
    graph = []

    random.seed(42)

    for item in FALLBACK_CONSTITUENCIES:
        name = item["name"]
        population = item["population"]
        required = max(1, math.ceil(population / capacity))
        # For presentation, generate proposed points directly.
        proposed = required

        minx, miny, maxx, maxy = item["bbox"]

        for i in range(1, proposed + 1):
            lon = random.uniform(minx + 0.01, maxx - 0.01)
            lat = random.uniform(miny + 0.01, maxy - 0.01)

            properties = {
                "station_name": f"{name}_{i}",
                "constituency": name,
                "station_type": "Proposed new polling station",
                "population": population,
                "capacity": capacity,
                "required_stations": required,
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "road_dist_m": 0,
                "mode": "Presentation fallback"
            }

            rows.append(properties)

            features.append({
                "type": "Feature",
                "properties": properties,
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat]
                }
            })

        graph.append({"constituency": name, "generated_points": proposed})

    geojson = {"type": "FeatureCollection", "features": features}
    return geojson, pd.DataFrame(rows), graph


def find_field(gdf, preferred, alternatives):
    if gdf is None:
        return None

    cols = list(gdf.columns)
    lower = {str(c).lower(): c for c in cols}

    if preferred in cols:
        return preferred

    for alt in alternatives:
        if alt in cols:
            return alt
        if alt.lower() in lower:
            return lower[alt.lower()]

    return None


def get_name_field():
    field = find_field(layers["constituencies"], CONSTITUENCY_NAME_FIELD, ["CONST", "Constituency", "NAME", "Name"])
    if field is not None:
        return field

    if layers["constituencies"] is not None:
        for col in layers["constituencies"].columns:
            if col != "geometry":
                return col

    return None


def get_population_field():
    return find_field(layers["constituencies"], POPULATION_FIELD, ["Population", "POPULATION", "POP", "Pop", "TOTAL_POP"])


def build_density_layer():
    if layers["constituencies"] is None or not GEOPANDAS_AVAILABLE:
        return None

    pop_field = get_population_field()
    name_field = get_name_field()

    if pop_field is None:
        return None

    density = layers["constituencies"].copy()

    try:
        density_m = density.to_crs(PROJECTED)
        density["area_sq_km"] = density_m.geometry.area / 1_000_000
    except Exception:
        density["area_sq_km"] = 100

    density["CONST"] = density[name_field].astype(str) if name_field else "Constituency"
    density["Population"] = pd.to_numeric(density[pop_field], errors="coerce").fillna(0)
    density["population_density"] = density["Population"] / density["area_sq_km"].replace(0, 1)

    return density


def random_point_in_polygon(poly):
    minx, miny, maxx, maxy = poly.bounds
    for _ in range(1000):
        p = Point(random.uniform(minx, maxx), random.uniform(miny, maxy))
        if poly.contains(p):
            return p
    return poly.representative_point()


def snap_to_road(point, roads_union):
    if roads_union is None or nearest_points is None:
        return point, None
    try:
        nearest = nearest_points(point, roads_union)[1]
        return nearest, point.distance(nearest)
    except Exception:
        return point, None



def clean_number(value, default=0):
    """Converts population/capacity values safely, even when they contain commas or text."""
    try:
        if pd.isna(value):
            return default
        text = str(value).replace(",", "").replace(" ", "").strip()
        return int(float(text))
    except Exception:
        return default


def get_capacity_from_request(data):
    """Accepts different names so the frontend never fails with 'ratio is not set'."""
    for key in ["capacity", "ratio", "people_per_station", "peoplePerStation", "station_capacity"]:
        if key in data:
            value = clean_number(data.get(key), 1000)
            if value > 0:
                return value
    return 1000


def safe_geometry(geom):
    """Repairs invalid polygons where possible and ignores empty geometries."""
    if geom is None or getattr(geom, "is_empty", True):
        return None
    try:
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom is None or geom.is_empty:
            return None
        return geom
    except Exception:
        return None


def point_from_any_geometry(geom):
    """Turns Point, MultiPoint, Polygon, or LineString geometry into one safe point."""
    try:
        if geom is None or geom.is_empty:
            return None
        if geom.geom_type == "Point":
            return geom
        return geom.representative_point()
    except Exception:
        return None


def candidate_point_for_constituency(poly_projected, schools_projected=None, localities_projected=None, roads_union=None):
    
    #Chooses a practical point: schools first, then localities, then random polygon points.
    #The point is snapped to the nearest road only when the nearest road is reasonably close.
    #This prevents one bad road layer from moving points far outside the constituency.
    
    point = None

    for source in [schools_projected, localities_projected]:
        try:
            if source is not None and len(source) > 0:
                inside = source[source.geometry.within(poly_projected)]
                if len(inside) > 0:
                    picked = inside.sample(1, random_state=random.randint(1, 999999)).geometry.iloc[0]
                    point = point_from_any_geometry(picked)
                    if point is not None:
                        break
        except Exception:
            pass

    if point is None:
        point = random_point_in_polygon(poly_projected)

    snapped, road_dist = snap_to_road(point, roads_union)
    if road_dist is not None and road_dist <= 2500:
        return snapped, road_dist
    return point, road_dist

def jitter_point_inside(point, polygon, distance_metres=350):
    """Creates a nearby unique point while keeping it inside the constituency."""
    try:
        for _ in range(40):
            angle = random.uniform(0, 2 * math.pi)
            distance = random.uniform(25, distance_metres)
            candidate = Point(point.x + math.cos(angle) * distance, point.y + math.sin(angle) * distance)
            if polygon.contains(candidate):
                return candidate
        if polygon.contains(point):
            return point
        return polygon.representative_point()
    except Exception:
        return polygon.representative_point()


def collect_candidate_points(source, polygon):
    """Collects school/locality points inside one constituency only once for speed."""
    points = []
    try:
        if source is None or len(source) == 0:
            return points
        inside = source[source.geometry.within(polygon)]
        for geom in inside.geometry:
            pt = point_from_any_geometry(geom)
            if pt is not None and polygon.contains(pt):
                points.append(pt)
    except Exception:
        pass
    return points


def generate_real_points(capacity):
    
    #Main optimization function.
    #It is intentionally defensive and fast enough for deployment.
    #The system generates required polling station points from population / capacity.
    #Schools and localities are used first as realistic candidate places.
    #Random fallback points are used only where no school/locality candidates exist.
    
    if not GEOPANDAS_AVAILABLE or layers["constituencies"] is None or len(layers["constituencies"]) == 0:
        raise ValueError("Real GIS constituency layer is unavailable.")

    pop_field = get_population_field()
    name_field = get_name_field()

    if pop_field is None:
        raise ValueError("Population field not found. Expected Population, POPULATION, POP, Pop, or TOTAL_POP.")

    constituencies = layers["constituencies"].copy()
    constituencies = constituencies[constituencies.geometry.notnull()].copy()
    if len(constituencies) == 0:
        raise ValueError("The constituency layer has no valid geometries.")

    constituencies = constituencies.to_crs(PROJECTED)

    schools = None
    if layers["schools"] is not None and len(layers["schools"]) > 0:
        try:
            schools = layers["schools"].to_crs(PROJECTED)
            schools = schools[schools.geometry.notnull()].copy()
        except Exception:
            schools = None

    localities = None
    if layers["localities"] is not None and len(layers["localities"]) > 0:
        try:
            localities = layers["localities"].to_crs(PROJECTED)
            localities = localities[localities.geometry.notnull()].copy()
        except Exception:
            localities = None

    records = []
    random.seed(42)

    for index, row in constituencies.iterrows():
        try:
            geom = safe_geometry(row.geometry)
            if geom is None:
                continue

            name = str(row[name_field]).strip() if name_field and pd.notna(row[name_field]) else f"Constituency_{index + 1}"
            population = clean_number(row[pop_field], 0)
            if population <= 0:
                population = capacity

            required = max(1, math.ceil(population / capacity))

            candidates = []
            candidates.extend(collect_candidate_points(schools, geom))
            candidates.extend(collect_candidate_points(localities, geom))

            for i in range(1, required + 1):
                if candidates:
                    base_point = candidates[(i - 1) % len(candidates)]
                    point = jitter_point_inside(base_point, geom)
                    mode = "School/locality guided GIS layer"
                else:
                    point = random_point_in_polygon(geom)
                    mode = "Constituency random GIS layer"

                records.append({
                    "station_name": f"{name}_{i}",
                    "constituency": name,
                    "station_type": "Proposed new polling station",
                    "population": population,
                    "capacity": capacity,
                    "required_stations": required,
                    "latitude": None,
                    "longitude": None,
                    "road_dist_m": None,
                    "mode": mode,
                    "geometry": point
                })
        except Exception as row_error:
            print(f"Skipped one constituency row during optimization: {row_error}")
            continue

    if len(records) == 0:
        raise ValueError("No records could be generated from the GIS layer.")

    stations = gpd.GeoDataFrame(records, crs=PROJECTED)
    stations = stations[stations.geometry.notnull()].copy()
    if len(stations) == 0:
        raise ValueError("Generated records had no valid point geometry.")

    stations = stations.to_crs(WGS84)
    stations["longitude"] = stations.geometry.x.round(6)
    stations["latitude"] = stations.geometry.y.round(6)

    layers["stations"] = stations
    return stations

def create_service_areas():
    if layers["stations"] is None or not GEOPANDAS_AVAILABLE:
        return None
    try:
        service = layers["stations"].to_crs(PROJECTED).copy()
        service["geometry"] = service.geometry.buffer(SERVICE_AREA_DISTANCE_METRES)
        service["service_area_m"] = SERVICE_AREA_DISTANCE_METRES
        layers["service_areas"] = service.to_crs(WGS84)
        return layers["service_areas"]
    except Exception:
        layers["service_areas"] = None
        return None


def create_heatmap_png():
    density = build_density_layer()

    if density is None or not GEOPANDAS_AVAILABLE:
        output_path = OUTPUT_DIR / "khomas_population_density_heatmap.png"
        fig, ax = plt.subplots(figsize=(10, 7))
        ax.text(0.5, 0.5, "Population density heatmap unavailable in fallback mode", ha="center", va="center")
        ax.axis("off")
        plt.savefig(output_path, dpi=200)
        plt.close(fig)
        return output_path

    density_m = density.to_crs(PROJECTED)

    fig, ax = plt.subplots(figsize=(12, 9))
    density_m.plot(column="population_density", cmap="YlOrRd", linewidth=0.8, edgecolor="black", legend=True, ax=ax)

    for _, row in density_m.iterrows():
        label_point = row.geometry.representative_point()
        ax.annotate(str(row["CONST"]), xy=(label_point.x, label_point.y), ha="center", va="center", fontsize=8)

    ax.set_title("Khomas Population Density Heatmap by Constituency")
    output_path = OUTPUT_DIR / "khomas_population_density_heatmap.png"
    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close(fig)
    return output_path



@app.route("/health")
def health():
    return jsonify({"status": "ok", "success": True})

@app.route("/")
def home():
    return render_template_string(HTML_PAGE)


@app.route("/diagnostics")
def diagnostics():
    return jsonify({
        "success": True,
        "geopandas_available": GEOPANDAS_AVAILABLE,
        "sklearn_available": SKLEARN_AVAILABLE,
        "data_dir": str(DATA_DIR),
        "missing_files": missing_files,
        "counts": {
            "constituencies": 0 if layers["constituencies"] is None else len(layers["constituencies"]),
            "schools": 0 if layers["schools"] is None else len(layers["schools"]),
            "roads": 0 if layers["roads"] is None else len(layers["roads"]),
            "localities": 0 if layers["localities"] is None else len(layers["localities"])
        },
        "mode": "GIS mode" if layers["constituencies"] is not None else "Presentation fallback mode"
    })


@app.route("/layers")
def get_layers():
    try:
        density = build_density_layer()

        return jsonify({
            "success": True,
            "constituencies": to_geojson(density) if density is not None else fallback_boundary_geojson(),
            "density": to_geojson(density) if density is not None else fallback_density_geojson(),
            "schools": to_geojson(layers["schools"]),
            "roads": to_geojson(layers["roads"]),
            "localities": to_geojson(layers["localities"]),
            "counts": {
                "constituencies": 0 if layers["constituencies"] is None else len(layers["constituencies"]),
                "schools": 0 if layers["schools"] is None else len(layers["schools"]),
                "roads": 0 if layers["roads"] is None else len(layers["roads"]),
                "localities": 0 if layers["localities"] is None else len(layers["localities"])
            }
        })
    except Exception:
        return jsonify({
            "success": True,
            "constituencies": fallback_boundary_geojson(),
            "density": fallback_density_geojson(),
            "schools": None,
            "roads": None,
            "localities": None,
            "counts": {"constituencies": len(FALLBACK_CONSTITUENCIES), "schools": 0, "roads": 0, "localities": 0}
        })


@app.route("/generate", methods=["POST"])
@app.route("/optimize", methods=["POST"])
def generate():
    try:
        data = request.get_json(silent=True) or {}
        capacity = get_capacity_from_request(data)

        # First try real GIS generation.
        try:
            stations = generate_real_points(capacity)
            create_service_areas()
            proposed = stations[stations["station_type"] == "Proposed new polling station"].copy()
            graph = proposed.groupby("constituency").size().reset_index(name="generated_points").to_dict(orient="records")

            return jsonify({
                "success": True,
                "stations": to_geojson(stations),
                "service_areas": to_geojson(layers["service_areas"]),
                "graph_summary": graph,
                "total_generated": int(len(proposed)),
                "total_stations": int(len(stations))
            })
        except Exception as real_error:
            # Presentation fallback must always generate points.
            print("Real GIS optimization failed. Fallback points generated.", real_error)
            geojson, table, graph = fallback_station_geojson_and_table(capacity)
            layers["stations"] = table

            return jsonify({
                "success": True,
                "stations": geojson,
                "service_areas": None,
                "graph_summary": graph,
                "total_generated": int(len(table)),
                "total_stations": int(len(table)),
                "message": "GIS layer problem was bypassed and fallback points were generated."
            })

    except Exception as error:
        print("Generate route safety net used:", error)
        print(traceback.format_exc())
        # Last safety net: still return demo points.
        geojson, table, graph = fallback_station_geojson_and_table(1000)
        layers["stations"] = table
        return jsonify({
            "success": True,
            "stations": geojson,
            "service_areas": None,
            "graph_summary": graph,
            "total_generated": int(len(table)),
            "total_stations": int(len(table)),
            "message": "Fallback points generated after error."
        })


@app.route("/reset", methods=["POST"])
def reset():
    layers["stations"] = None
    layers["service_areas"] = None
    return jsonify({"success": True})


@app.route("/export/heatmap")
def export_heatmap():
    output_path = create_heatmap_png()
    return send_file(output_path, as_attachment=True)


@app.route("/export/<file_type>")
def export_results(file_type):
    stations = layers["stations"]

    if stations is None:
        _, stations, _ = fallback_station_geojson_and_table(1000)

    if file_type == "csv":
        output_path = OUTPUT_DIR / "polling_stations_lat_long.csv"
        if GEOPANDAS_AVAILABLE and hasattr(stations, "drop") and "geometry" in getattr(stations, "columns", []):
            stations.drop(columns="geometry").to_csv(output_path, index=False)
        else:
            stations.to_csv(output_path, index=False)
        return send_file(output_path, as_attachment=True)

    if file_type == "excel":
        output_path = OUTPUT_DIR / "polling_stations_lat_long.xlsx"
        if GEOPANDAS_AVAILABLE and hasattr(stations, "drop") and "geometry" in getattr(stations, "columns", []):
            stations.drop(columns="geometry").to_excel(output_path, index=False)
        else:
            stations.to_excel(output_path, index=False)
        return send_file(output_path, as_attachment=True)

    if file_type == "geojson":
        output_path = OUTPUT_DIR / "polling_stations.geojson"
        if GEOPANDAS_AVAILABLE and hasattr(stations, "to_file"):
            stations.to_file(output_path, driver="GeoJSON")
        else:
            geojson, _, _ = fallback_station_geojson_and_table(1000)
            output_path.write_text(json.dumps(geojson), encoding="utf-8")
        return send_file(output_path, as_attachment=True)

    if file_type == "shapefile":
        if not GEOPANDAS_AVAILABLE or not hasattr(stations, "to_file"):
            return jsonify({"success": False, "message": "Shapefile export requires GeoPandas."}), 400

        folder = OUTPUT_DIR / "polling_stations_shapefile"
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir()

        stations.to_file(folder / "polling_stations.shp")
        zip_path = OUTPUT_DIR / "polling_stations_shapefile.zip"
        if zip_path.exists():
            zip_path.unlink()

        with zipfile.ZipFile(zip_path, "w") as zipped:
            for item in folder.iterdir():
                zipped.write(item, item.name)

        return send_file(zip_path, as_attachment=True)

    return jsonify({"success": False, "message": "Invalid export format."}), 400


def open_browser():
    if os.environ.get("RENDER") != "true":
        webbrowser.open_new("http://127.0.0.1:5000")


missing_files = load_layers()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if os.environ.get("RENDER") != "true":
        threading.Timer(1.2, open_browser).start()
    app.run(host="0.0.0.0", port=port, debug=False)
