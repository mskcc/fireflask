from flask import Flask, render_template, request,jsonify,redirect
from fireworks import Firework
from fireworks.utilities.fw_serializers import DATETIME_HANDLER
from pymongo import DESCENDING, MongoClient
import os, json, sys
#oh shitttt son
from launchpad import FlaskPad
from flask.ext.paginate import Pagination
###############################
#from tornado.wsgi import WSGIContainer
#from tornado.httpserver import HTTPServer
#from tornado.ioloop import IOLoop
#from . import app

#http_server = HTTPServer(WSGIContainer(app))
#http_server.listen(5000)
#IOLoop.instance().start()
app = Flask(__name__)
app.use_reloader=True
CMO_CONFIG_LOC="/opt/common/CentOS_6-dev/cmo"
hello = __name__
lp = FlaskPad.from_file(CMO_CONFIG_LOC + "/cmo.yaml")
PER_PAGE = 20
STATES = Firework.STATE_RANKS.keys()
client = MongoClient(host="plvcbiocmo2.mskcc.org", port=27017)
dbnames = client.database_names()
for administrative_db in ["admin", "local", "test", "daemons"]:
    dbnames.remove(administrative_db)

#FIXME replace ith CMO method

def get_dbconfig(db_name):
    db_cfg = os.path.join(CMO_CONFIG_LOC, db_name + ".yaml")
    if os.path.exists(db_cfg):
       return db_cfg
    return None

@app.template_filter('datetime')
def datetime(value):
    import datetime as dt

    date = dt.datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%f')
    return date.strftime('%m/%d/%Y')


@app.template_filter('pluralize')
def pluralize(number, singular='', plural='s'):
    if number == 1:
        return singular
    else:
        return plural

@app.route("/", methods=['GET'])
@app.route("/<dbname>", methods=['GET'])
@app.route("/<dbname>/", methods=['GET'])
def home(dbname=None):
    db_names = dbnames  
    fw_nums = []
    wf_nums = []
    selected = None
    if not dbname:
        return redirect("/cmo")
    else:
       db_name=dbname
    for state in STATES:
        fw_nums.append(lp.get_fw_ids(dbname, query={'state': state}, count_only=True))
        wf_nums.append(lp.get_wf_ids(dbname,query={'state': state}, count_only=True))
    state_nums = zip(STATES, fw_nums, wf_nums)

    tot_fws = sum(fw_nums)
    tot_wfs = sum(wf_nums)

    # Newest Workflows table data
    wfs_shown = lp.client[dbname].workflows.find({}, limit=PER_PAGE, sort=[('_id', DESCENDING)])
    wf_info = []
    for item in wfs_shown:
        wf_info.append({
            "id": item['nodes'][0],
            "name": item['name'],
            "state": item['state'],
            "fireworks": list(lp.client[dbname].fireworks.find({"fw_id": {"$in": item["nodes"]}},
                                                limit=PER_PAGE, sort=[('fw_id', DESCENDING)],
                                                projection=["state", "name", "fw_id"]))
        })
    return render_template('home.html', **locals())

@app.route('/<dbname>/wf/<int:wf_id>/delete')
def delete_wf(dbname, wf_id):
    try:
        wf_id=int(wf_id)
    except: 
        raise ValueError("Invalid wf_id: {}".format(wf_id))
    try:
        lp.delete_wf(dbname, wf_id)
        return jsonify({ "status": "success" })
    except:
        return jsonify({ "status": "failed" })




@app.route('/<dbname>/fw/<int:fw_id>')
def show_fw(dbname, fw_id):
    db_name=dbname
    global dbnames
    db_names = dbnames
    try:
        int(fw_id)
    except:
        raise ValueError("Invalid fw_id: {}".format(fw_id))
    fw = lp.get_fw_dict_by_id(dbname, fw_id)
    command = None
    if '_tasks' in fw['spec']:
        if 'script' in fw['spec']['_tasks'][0]:
            command = fw['spec']['_tasks'][0]['script'][0]
    bsub_options  = {}
    if '_queueadapter' in fw['spec']:
            bsub_options = fw['spec']['_queueadapter']
    fw = json.loads(json.dumps(fw, default=DATETIME_HANDLER))  # formats ObjectIds
    return render_template('fw_details.html', **locals())


@app.route('/<dbname>/wf/<int:wf_id>')
def show_workflow(dbname, wf_id):
    global dbnames
    db_names = dbnames
    db_name = dbname
    try:
        int(wf_id)
    except ValueError:
        raise ValueError("Invalid fw_id: {}".format(wf_id))
    wf = lp.get_wf_summary_dict(dbname,wf_id)
    wf = json.loads(json.dumps(wf, default=DATETIME_HANDLER))  # formats ObjectIds
    return render_template('wf_details.html', **locals())


@app.route('/<dbname>/fw/', defaults={"state": "total"})
@app.route("/<dbname>/fw/<state>/")
def fw_states(dbname, state):
    global dbnames
    db_names=dbnames
    db_name = dbname
    db = lp.client[dbname].fireworks
    q = {} if state == "total" else {"state": state}
    fw_count = lp.get_fw_ids(dbname, query=q, count_only=True)
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    rows = list(db.find(q, projection=["fw_id", "name", "created_on"]).sort([('_id', DESCENDING)]).skip(page - 1).limit(
        PER_PAGE))
    pagination = Pagination(page=page, total=fw_count, record_name='fireworks', per_page=PER_PAGE)
    all_states = STATES
    return render_template('fw_state.html', **locals())


@app.route('/<dbname>/wf/', defaults={"state": "total"})
@app.route("/<dbname>/wf/<state>/")
def wf_states(dbname, state):
    global dbnames
    db_names=dbname
    db_name = dbname
    db = lp.client[dbname].workflows
    q = {} if state == "total" else {"state": state}
    wf_count = lp.get_fw_ids(dbname, query=q, count_only=True)
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    rows = list(db.find(q).sort([('_id', DESCENDING)]).skip(page - 1).limit(PER_PAGE))
    for r in rows:
        r["fw_id"] = r["nodes"][0]
    pagination = Pagination(page=page, total=wf_count, record_name='workflows', per_page=PER_PAGE)
    all_states = STATES
    return render_template('wf_state.html', **locals())


if __name__ == "__main__":
    app.run(debug=True, host="plvcbiocmo2.mskcc.org", port=8080)
