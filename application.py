import backend
import imagery
import config_reader
import os
import shutil
import geolocation
import numpy as np
import json
from detectors.Detector import Detector
from Mask_RCNN_Detect import Mask_RCNN_Detect
from PIL import Image
from flask import Flask, render_template, request, flash, redirect, url_for, send_from_directory, send_file

application = Flask(__name__)

imd = None
program_config = None
osm = None
mrcnn = None

# gets the directories all set up
if (os.path.isdir('runtime')):
    shutil.rmtree('runtime')
os.mkdir('runtime')
os.mkdir('runtime/images')
os.mkdir('runtime/masks')

# useful function for turning request data into usable dictionaries
def result_to_dict(result):
    info = {}
    for k, v in result.items():
        info[k.lower()] = v
    return info

@application.route('/', methods=['GET'])  # base page that loads up on start/accessing the website
def login():  # this method is called when the page starts up
    return redirect('/home/')

@application.route('/home/')
def home(lat=None, lng=None, zoom=None):
    # necessary so that if one refreshes, the way memory deletes with the drawn polygons
    global osm, program_config
    osm.clear_ways_memory()
    Detector.reset()

    if lat is None or lng is None or zoom is None:
        config = program_config
        lat = config['start_lat']
        lng = config['start_lng']
        zoom = config['start_zoom']

    access_key = program_config['accessKey']
    context = {}
    context['lat'] = lat
    context['lng'] = lng
    context['zoom'] = zoom
    context['access_key'] = access_key

    return render_template('DisplayMap.html', **context)

@application.route('/<zoom>/<lat>/<lng>', methods=['GET'])
def move_to_new_lat_long(zoom, lat, lng):
    return home(zoom, lat, lng)

@application.route('/home/backendWindow/', methods=['POST', 'GET'])
def backend_window():
    global mrcnn
    if mrcnn is None or mrcnn.image_id == 1: # no images masked yet
        return send_from_directory('default_images', 'default_window.jpeg')
    print("in backend window", mrcnn.image_id)
    print('looking at ' + 'mask_{}.png'.format(mrcnn.image_id-1))
    return send_from_directory('runtime/masks', 'mask_{}.png'.format(mrcnn.image_id-1))

@application.route('/home/detect_buildings', methods=['POST'])
def mapclick():
    global osm, mcrnn, imd
    if request.method == 'POST':
        result = request.form
        info = result_to_dict(result)

        lat = float(info['lat'])
        lng = float(info['lng'])
        zoom = int(info['zoom'])
        strategy = info['strategy']

        # find xtile, ytile
        xtile, ytile = geolocation.deg_to_tile(lat, lng, zoom)
        image = np.array(imd.download_tile(xtile, ytile, zoom))

        if strategy == 'mrcnn':
            # SETUP MRCNN STUFF
            global mrcnn
            if mrcnn is None: # import if not already imported
                print('import MRCNN stuff...')
                from Mask_RCNN_Detect import Mask_RCNN_Detect
                mrcnn = Mask_RCNN_Detect('weights/epoch55.h5')

            mask_data = mrcnn.detect_building(image, lat, lng, zoom)
            building_ids = list(mask_data.keys())
            building_points = list(mask_data.values())

        else:
            detector = Detector(image, lat, lng, zoom)
            rect_id, rect_points = detector.detect_building()
            building_ids = [rect_id]
            building_points = [rect_points]

        json_post = {"rects_to_add": [{
                                "ids": building_ids,
                                "points": building_points
                            }],
            "rects_to_delete": {"ids": []}
                    }

    return json.dumps(json_post)

@application.route('/home/delete_building', methods=['POST'])
def delete_building():
    result = request.form
    info = result_to_dict(result)
    building_id = None
    lat = None
    lng = None
    zoom = None

    building_id = None
    if 'building_id' in info:
        building_id = int(info['building_id'])
    else:
        lat = float(info['lat'])
        lng = float(info['lng'])
        zoom = float(info['zoom'])

    global mrcnn
    if mrcnn is not None:
        building_id = mrcnn.delete_mask(lat, lng, zoom, building_id)

        json_post = {"rects_to_delete":
                        {"ids": [building_id]}
                    }
        return json.dumps(json_post)

    return 'mrcnn has not been made'

@application.route('/home/upload', methods=['POST'])
def upload_changes():
    print('uploading to OSM...')
    global osm
    
    # # Create the way using the list of nodes
    changeset_comment = "Added " + str(len(mrcnn.id_geo)) + " buildings."
    print("comment", changeset_comment)
    ways_created = osm.way_create_multiple(mrcnn.id_geo, changeset_comment, {"building": "yes"})
    
    # # Clear the rectangle list
    mrcnn.clear()
    print('uploaded!')
    return str(len(ways_created))

@application.route('/home/OSMSync', methods=['POST'])
def OSM_map_sync():
    if request.method == 'POST':
        result = request.form
        info = result_to_dict(result)

        min_long = float(info['min_long'])
        min_lat = float(info['min_lat'])
        max_long = float(info['max_long'])
        max_lat = float(info['max_lat'])

        global osm
        mapplicationable_results = osm.sync_map(min_long, min_lat, max_long, max_lat)
        if mapplicationable_results == None or len(mapplicationable_results) == 0:
            json_post = {'rectsToAdd': []}
            return json.dumps(json_post)
        # note that this is in a different format as the other json_post for a map click
        # mapplicationable_results is a list with each index a building containing tuples for the coordinates of the corners
        json_post = {"rectsToAdd": mapplicationable_results}
        return json.dumps(json_post)

@application.route('/home/citySearch', methods=['POST'])
def citySearch():
    if request.method == 'POST':
        result = request.form
        info = result_to_dict(result)
        print('info', info)
        city_name = info['query']
        coords = backend.search_city(city_name)

        if coords != None:
            json_post = {'lat': coords[0],
                          'lng': coords[1]}
            return json.dumps(json_post)

        json_post = {'lat': '-1000'}
        return json.dumps(json_post)

# run the application.
if __name__ == "__main__":
    config = config_reader.get_config()

    # useless
    application.secret_key = 'super secret key'
    
    # Get config variables
    access_key = None
    if "accessKey" in config:
        access_key = config["accessKey"]

    # Create imagery downloader
    imd = imagery.ImageryDownloader(access_key)
    
    program_config = config

    init_info = program_config["osmUpload"]
    args = ["api", "username", "password"]
    for arg in args:
        if arg not in init_info:
            print("[ERROR] Config: osmUpload->" + arg + " not found!")
            raise ValueError()

    # initializes the class for interacting with OpenStreetMap's API
    osm = backend.OSM_Interactor(init_info["api"], init_info["username"], init_info["password"])

    application.debug = True
    application.run()
