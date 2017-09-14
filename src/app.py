import datetime
from datetime import timedelta
from functools import wraps
import json
import os
import base64
import urlparse
# OrderedDict is returned by the API reports (see `eval(response)` below)
# leave this import
from collections import OrderedDict

from flask import (Flask, request, flash, session, redirect, render_template,
                   url_for, jsonify, make_response, Response)
from flask.ext.session import Session
import memcache
import PyRSS2Gen
import requests

from utils import (conversions, deep_update, is_num, gen_nested_dict, User,
                   format_messages, Order, Scene)
from logger import ilogger as logger


memcache_hosts = os.getenv('ESPA_MEMCACHE_HOST', '127.0.0.1:11211').split(',')
cache = memcache.Client(memcache_hosts, debug=0)

espaweb = Flask(__name__)
espaweb.config.from_envvar('ESPAWEB_SETTINGS', silent=False)
espaweb.secret_key = espaweb.config['SECRET_KEY']
espaweb.config['SESSION_TYPE'] = 'memcached'
espaweb.config['SESSION_MEMCACHED'] = cache
espaweb.config['SESSION_PERMANENT'] = False
espaweb.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=120)
espaweb.config['SESSION_COOKIE_SECURE'] = False

Session(espaweb)
api_base_url = os.getenv('ESPA_API_HOST', 'http://localhost:4004/api/v1')

def api_up(url, json=None, verb='get', uauth=None):
    headers = {'X-Forwarded-For': request.remote_addr}
    auth_tup = uauth if uauth else (session['user'].username, session['user'].wurd)
    retdata = dict()
    try:
        response = getattr(requests, verb)(api_base_url + url, json=json,
                                           auth=auth_tup, headers=headers)
        retdata = response.json()
    except Exception as e:
        logger.error('+! Unable to contact API !+')
        flash('Critical error contacting ESPA-API', 'error')
    if isinstance(retdata, dict):
        messages = retdata.pop('messages', dict())
        if 'errors' in messages:
            flash(format_messages(messages.get('errors')), 'error')
        if 'warnings' in messages:
            flash(format_messages(messages.get('warnings')), 'warning')
    return retdata


def update_status_details(force=False):
    cache_key = 'espa_web_system_status'  # WARNING: cached across all sessions
    status_response = cache.get(cache_key)
    fifteen_minutes = 900  # seconds
    if (status_response is None) or force:
        status_response = api_up('/info/status')
        cache.set(cache_key, status_response, fifteen_minutes)
    for item in ['system_message_body', 'system_message_title', 'display_system_message']:
        session[item] = status_response.get(item)

    item = 'stat_backlog_depth'
    cache_statkey = cache_key + item
    stat_resp = cache.get(cache_statkey)
    if (stat_resp is None) or force:
        stat_resp = api_up('/info/backlog')
        cache.set(cache_statkey, stat_resp, fifteen_minutes)
    session[item] = stat_resp


def staff_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        is_staff = False
        if 'user' in session and session.get('user', None):
            is_staff = session['user'].is_staff

        if is_staff is False:
            flash('staff only', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or session['logged_in'] is not True:
            return redirect(url_for('login', next=request.full_path))
        return f(*args, **kwargs)
    return decorated_function


def espa_session_login(username, password):
    cache_key = '{}_web_credentials'.format(username.replace(' ', '_'))
    resp_json = cache.get(cache_key)
    if (resp_json is None) or (isinstance(resp_json, dict) and resp_json.get('wurd') != password):
        resp_json = api_up("/user", uauth=(username, password))

    if 'username' in resp_json:
        session['logged_in'] = True
        resp_json['wurd'] = password
        session['user'] = User(**resp_json)

        two_hours = 7200  # seconds
        cache.set(cache_key, resp_json, two_hours)

        update_status_details()
        logger.info("User %s logged in" % session['user'].username)
        return True
    else:
        logger.info("**** Failed user login %s" % username)
        return False


@espaweb.route('/login', methods=['GET', 'POST'])
def login():
    destination = request.args.get('next')
    if request.method == 'POST':
        username, password = request.form['username'], request.form['password']
        if espa_session_login(username, password):
            # send the user back to their
            # originally requested destination
            if destination and destination != 'None':
                return redirect(urlparse.urlparse(destination).path)
            else:
                return redirect(url_for('index'))
        else:
            _status = 401
    else:
        _status = 200
        if 'user' not in session:
            session['user'] = None

    explorer = espaweb.config.get('earth_explorer', 'https://earthexplorer.usgs.gov')
    reg_host = espaweb.config.get('eros_registration_system', 'https://ers.cr.usgs.gov')

    return render_template('login.html', next=destination,
                           earthexplorer=explorer,
                           register_user=reg_host+"/register",
                           forgot_login=reg_host+"/password/request"), _status


@espaweb.route('/logout')
def logout():
    if 'user' not in session:
        logger.info('No user session found.')
        return redirect(url_for('login'))
    logger.info("Logging out user %s \n" % session['user'].username)
    cache_key = '{}_web_credentials'.format(session['user'].username.replace(' ', '_'))
    cache.delete(cache_key)
    for item in ['logged_in', 'user', 'system_message_body', 'system_message_title',
                 'stat_products_complete_24_hrs', 'stat_backlog_depth']:
        session.pop(item, None)
    return redirect(url_for('login'))


@espaweb.route('/')
@espaweb.route('/index/')
@login_required
def index():
    return render_template('index.html')


@espaweb.route('/ordering/new/')
@login_required
def new_order():
    return render_template('new_order.html', form_action=url_for('submit_order'))


@espaweb.route('/ordering/new_external/', methods=['GET','POST'])
def new_external_order():
    if request.method == 'POST':
        data = request.form.to_dict()
        try:
            scenelist = data['input_product_list']
            _u, _p = base64.b64decode(data['user']).split(':')
            espa_session_login(_u, _p)
        except KeyError:
            return jsonify({'error': "'input_product_list' and 'user' fields are required"}), 401
        except Exception as e:
            logger.info("*** espa-web exception - problem parsing external order request. message: {}".format(e.message))
            return jsonify({'error': 'problem parsing request'}), 400
    else:
        # GET redirect from ESPA after order validation
        scenelist = session['ipl']

    return render_template('new_order.html',
                           form_action=url_for('submit_order'),
                           scenelist=scenelist)


@espaweb.route('/ordering/submit/', methods=['POST'])
@login_required
def submit_order():
    # form values come in as an ImmutableMultiDict
    data = request.form.to_dict()
    logger.info("* new order submission for user %s\n\n order details: %s\n\n\n" % (session['user'].username, data))
    _external = False
    try:
        # grab sceneids from the file in input_product_list field
        _ipl_list = request.files.get('input_product_list').read().splitlines()
        _ipl = [i.strip().split("/r") for i in _ipl_list]
        _ipl = [item for sublist in _ipl for item in sublist if item]
    except AttributeError, e:
        # must be coming from new_external_order
        _ipl_list = data.pop('input_product_list')
        _ipl = _ipl_list.split(",")
        session['ipl'] = _ipl_list
        _external = True

    try:
        # convert our list of sceneids into format required for new orders
        scene_dict_all_prods = api_up("/available-products", json={'inputs': _ipl})
    except UnicodeDecodeError as e:
        flash('Decoding Error with input file. Please check input file encoding', 'error')
        logger.info("problem with order submission for user %s\n\n message: %s\n\n" % (session['user'].username,
                                                                                       e.message))

        return redirect(url_for('new_order'))
    finally:
        # These are errors, and the API will not recognize them on validation
        errors = []
        error_lookup = {'not_implemented': "Unknown IDs",
                        'date_restricted': "Missing Auxillary Data",
                        'ordering_restricted': "Pre-Collection unavailable"}
        for key in error_lookup:
            if key in scene_dict_all_prods:
                remove = scene_dict_all_prods.get(key)
                if isinstance(remove, dict):  # Must be date_restricted products
                    uniques = list(set([u for _, v in remove.items() for u in v]))
                    remove = '{}: {}'.format(json.dumps(remove.keys()),
                                             json.dumps(uniques))
                errors.append('{}. Invalid IDs must be removed: {}'
                              .format(error_lookup[key], remove))
        if errors:
            flash(format_messages(errors), category='error')
            return redirect(url_for('new_order'))

    # create a list of requested products
    landsat_list = [key for key in data if key in conversions['products']]
    # now that we have the product list, lets remove
    # this key from the form inputs
    for p in landsat_list:
        data.pop(p)

    # scrub the 'spectral_indices' value from data
    # used simply for toggling display of spectral indice products
    if 'spectral_indices' in data:
        data.pop('spectral_indices')

    # the image extents parameters also come in under
    # this key in the form, and this causes a conflict
    # with the 'image_extents' used to enable modifying
    # image extents in the deep_update function
    clk = ['image_extents', 'projection', 'resize']
    for k in clk:
        if k in data:
            # 'image_extents', 'projection', 'resize'
            # are in data.keys().  if there are child
            # keys, remove k
            if "{}|".format(k) in " ".join(data.keys()):
                data.pop(k)
        else:
            # project, resize, image_extents are not there
            # so remove their children
            for key in data.keys():
                if k in key:
                    data.pop(key)

    # this dictionary will hold the output
    out_dict = {}
    for k, v in data.iteritems():
        # all values coming in from the post request
        # are unicode, convert those values which
        # should be int or float
        tdict = gen_nested_dict(k.split("|"), is_num(v))
        # deep_update updates the dictionary
        deep_update(out_dict, tdict)

    # MODIS only receive l1 or stats
    modis_list = ['l1']
    if 'stats' in landsat_list:
        modis_list.append('stats')

    # Key here is usually the "sensor" name (e.g. "tm4") but can be other stuff
    for key in scene_dict_all_prods:
        if key.startswith('mod') or key.startswith('myd'):
            scene_dict_all_prods[key]['products'] = modis_list
        else:
            scene_dict_all_prods[key]['products'] = landsat_list

    # combine order options with product lists
    out_dict.update(scene_dict_all_prods)

    # keys to clean up
    cleankeys = ['target_projection']
    for item in cleankeys:
        if item in out_dict:
            if item == 'target_projection' and out_dict[item] == 'lonlat':
                # there are no other parameters needed for a geographic
                # projection, so assign 'lonlat' to the projection key
                out_dict['projection'] = {'lonlat': None}
            out_dict.pop(item)

    if 'plot_statistics' in out_dict:
        out_dict['plot_statistics'] = True

    logger.info('Order out to API: {}'.format(out_dict))
    response_data = api_up("/order", json=out_dict, verb='post')
    logger.info('Response from API: {}'.format(response_data))

    if 'orderid' in response_data:
        flash("Order submitted successfully! Your OrderId is {}".format(response_data['orderid']))
        logger.info("successful order submission for user %s\n\n orderid: %s" % (session['user'].username,
                                                                                 response_data['orderid']))
        rdest = redirect('/ordering/order-status/{}/'.format(response_data['orderid']))
    else:
        logger.info("problem with order submission for user {0}\n\n "
                    "message: {1}\n\n".format(session['user'].username,
                                              response_data.get('messages')))
        _dest = url_for('new_external_order') if _external else url_for('new_order')
        rdest = redirect(_dest)

    return rdest

@espaweb.route('/ordering/status/')
@espaweb.route('/ordering/status/<email>/')
@login_required
def list_orders(email=None):
    url = "/list-orders"
    for_user = session['user'].email
    if email:
        url += "/{}".format(email)
        for_user = email
    res_data = api_up(url, json={'status': ['complete', 'ordered']})
    if isinstance(res_data, list):
        res_data = sorted(res_data, reverse=True)

    complete_statuses = ['complete', 'unavailable', 'cancelled']

    backlog = 0
    order_info = []
    for orderid in res_data:
        order = api_up('/order/{}'.format(orderid))
        item_status = api_up('/item-status/{}'.format(orderid))
        item_status = item_status.get(orderid, {})
        item_status = map(lambda x: Scene(**x), item_status)
        count_ordered = len(item_status)
        count_complete = len([s for s in item_status if s.status == 'complete'])
        count_error = len([s for s in item_status if s.status == 'error'])
        order.update(products_ordered=count_ordered)
        order.update(products_complete=count_complete)
        order.update(products_error=count_error)
        order_info.append(Order(**order))
        backlog += (count_ordered - len([s for s in item_status
                                         if s.status in complete_statuses]))

    return render_template('list_orders.html', order_list=order_info,
                           for_user=for_user, backlog=backlog)


@espaweb.route('/ordering/status/<email>/rss/')
def list_orders_feed(email):
    # browser hit this url, need to handle # TODO: Is this still used?
    # user auth for both use cases
    if 'Authorization' in request.headers:  # FIXME: pretty sure this is gone
        # coming in from bulk downloader
        logger.info("Apparent bulk download attempt, headers: %s" % request.headers)
        auth_header_dec = base64.b64decode(request.headers['Authorization'])
        uauth = tuple(auth_header_dec.split(":"))
    else:
        if 'logged_in' not in session or session['logged_in'] is not True:
            return redirect(url_for('login', next=request.url))
        else:
            uauth = (session['user'].username, session['user'].wurd)
    orders = api_up("/list-orders/{}".format(email), uauth=uauth,
                    json={'status': 'complete'})

    order_items = dict()
    for orderid in orders:
        item_status = api_up('/item-status/{}'.format(orderid), uauth=uauth)
        item_status = item_status.get(orderid, {})
        item_status = map(lambda x: Scene(**x), item_status)
        order_info = api_up('/order/{}'.format(orderid), uauth=uauth)
        order_items[orderid] = dict(scenes=item_status,
                                    orderdate=order_info['order_date'])


    rss = PyRSS2Gen.RSS2(
        title='ESPA Status Feed',
        link='http://espa.cr.usgs.gov/ordering/status/{0}/rss/'.format(email),
        description='ESPA scene status for:{0}'.format(email),
        language='en-us',
        lastBuildDate=datetime.datetime.now(),
        items=[]
    )

    for orderid, order in order_items.items():
        for scene in order['scenes']:
            if scene.status != 'complete':
                continue
            description = ('scene_status:{0},orderid:{1},orderdate:{2}'
                           .format(scene.status, orderid, order['orderdate']))
            new_rss_item = PyRSS2Gen.RSSItem(
                title=scene.name,
                link=scene.product_dload_url,
                description=description,
                guid=PyRSS2Gen.Guid(scene.product_dload_url)
            )

            rss.items.append(new_rss_item)

    return rss.to_xml(encoding='utf-8')


@espaweb.route('/ordering/order-status/<orderid>/')
@login_required
def view_order(orderid):
    order_dict = Order(**api_up("/order/{}".format(orderid)))
    item_status = api_up('/item-status/{}'.format(orderid))
    item_status = item_status.get(orderid, {})
    scenes = map(lambda x: Scene(**x), item_status)

    statuses = {'complete': ['complete', 'unavailable'],
                'open': ['oncache', 'queued', 'processing', 'error', 'submitted'],
                'waiting': ['retry', 'onorder'],
                'error': ['error']}

    product_counts = {}
    for status in statuses:
        product_counts[status] = len([s for s in scenes
                                      if s.status in statuses[status]])
    product_counts['total'] = len(scenes)

    # get away from unicode
    joptions = json.dumps(order_dict.product_opts)

    # sensor/products
    options_by_sensor = {}
    for key in order_dict.product_opts:
        _kval = order_dict.product_opts[key]
        if isinstance(_kval, dict) and 'products' in _kval:
            _spl = _kval['products']
            _out_spl = [conversions['products'][item] for item in _spl]
            options_by_sensor[key] = _out_spl

    options_list = [" {0}: {1} ,".format(sensor, ", ".join(options_by_sensor[sensor])) for sensor in options_by_sensor]
    options_str = " ".join(options_list)

    return render_template('view_order.html', order=order_dict, scenes=scenes, prod_str=options_str,
                           product_counts=product_counts, product_opts=joptions)


@espaweb.route('/ordering/cancel_order/<orderid>', methods=['PUT'])
@login_required
def cancel_order(orderid):
    payload = {'orderid': orderid, 'status': 'cancelled'}
    response = api_up('/order', json=payload, verb='put')
    if response.get('orderid') == orderid:
        flash("Order cancelled successfully!")
        logger.info("order cancellation for user {0} ({1})\n\n orderid: {2}"
                    .format(session['user'].username, request.remote_addr,
                            response))
    return ''


@espaweb.route('/logfile/<orderid>/<sceneid>')
@staff_only
@login_required
def cat_logfile(orderid, sceneid):
    scenes_resp = api_up("/item-status/{}/{}".format(orderid, sceneid))
    scene = scenes_resp[orderid].pop()
    return '<br>'.join(scene['log_file_contents'].split('\n'))


@espaweb.route('/reports/')
@staff_only
@login_required
def list_reports():
    res_data = api_up("/reports/")
    return render_template('list_reports.html', reports=res_data)


@espaweb.route('/reports/<name>/')
@staff_only
@login_required
def show_report(name):
    response = api_up("/reports/{0}/".format(name))
    res_data = eval(response)
    # occassionally see UnicodeDecodeError in reports
    # lets decode the response
    for rpt in res_data:
      for k,v in rpt.items():
        rpt[k] = str(v).decode('utf8', 'strict')
    return render_template('report.html', report_name=name, report=res_data)


@espaweb.route('/admin_console', methods=['GET'])
@staff_only
@login_required
def admin_console():
    data = api_up("/statistics/all")
    stats = {'Open Orders': data['stat_open_orders'],
             'Products Complete 24hrs': data['stat_products_complete_24_hrs'],
             'Waiting Users': data['stat_waiting_users'],
             'Backlog Depth': data['stat_backlog_depth']}
    L17_aux = api_up("/aux_report/L17/")
    L8_aux = api_up("/aux_report/L8/")

    # Data gaps appearing in 2016, only one data source for L8
    now = datetime.datetime.now()
    years = range(now.year, now.year+1)

    gap_dict = {'L8': {'lads': {}}, 'L17': {'toms': {}, 'ncep': {}}}
    for key in ['toms', 'ncep', 'lads']:
        if key == 'lads':
            for yr in years:
                yr = str(yr)
                if yr in L8_aux.get(key, ''):
                    gap_dict['L8'][key][yr] = L8_aux[key][yr]
        else:
            for yr in years:
                yr = str(yr)
                if yr in L17_aux.get(key, ''):
                    gap_dict['L17'][key][yr] = L17_aux[key][yr]

    return render_template('console.html', stats=stats, gaps=gap_dict)


@espaweb.route('/admin_console/statusmsg', methods=['GET', 'POST'])
@staff_only
@login_required
def statusmsg():
    if request.method == 'POST':
        dsm = 'True' if 'display_system_message' in request.form else 'False'
        api_args = {'system_message_title': request.form['system_message_title'],
                    'system_message_body': request.form['system_message_body'],
                    'display_system_message': dsm}
        response = api_up('/system-status-update', api_args, verb='post')

        if response == 'success':
            update_status_details(force=True)
            flash('update successful')
            rurl = 'index'
        else:
            rurl = 'statusmsg'
        action = redirect(url_for(rurl))
    else:
        update_status_details()
        action = render_template('statusmsg.html')

    return action


@espaweb.route('/admin_console/config', methods=['GET'])
@staff_only
@login_required
def console_config():
    config_data = api_up("/system/config")
    sorted_keys = sorted(config_data)
    return render_template('config.html', config_data=config_data, sorted_keys=sorted_keys)


@espaweb.route('/adm/<action>/<orderid>', methods=['PUT'])
@staff_only
@login_required
def admin_update(action, orderid):
    return api_up('/{}/{}'.format(action, orderid), {}, 'put')


@espaweb.errorhandler(404)
def page_not_found(e):
    message = {'404: Not Found': ['The requested URL was not found on the '
                                  'server.', 'If you entered the URL manually '
                                  'please check your spelling and try again.']}
    flash(format_messages(message), category='warning')
    return render_template('base.html')


@espaweb.errorhandler(500)
def internal_error(e):
    message = {'500 Internal Server Error': ['Sorry, something went wrong.',
                                             'A programming error has caused '
                                             'the page to fail rendering.',
                                             'Contact the Site Admin ASAP']}
    flash(format_messages(message), category='error')
    return render_template('base.html')


@espaweb.after_request
def apply_xframe_options(response):
    response.headers['X-Frame-Options'] = 'DENY'
    return response

if __name__ == '__main__':
    debug = False
    if 'ESPA_DEBUG' in os.environ and os.environ['ESPA_DEBUG'] == 'True':
        debug = True
    espaweb.run(debug=debug, use_evalex=False, host='0.0.0.0', port=8889)

