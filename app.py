from flask import Flask, render_template, request,jsonify,redirect
from fireworks import Firework
from fireworks.utilities.fw_serializers import DATETIME_HANDLER
from pymongo import DESCENDING, MongoClient
import os, json, sys, glob
#oh shitttt son
from launchpad import FlaskPad
from flask.ext.paginate import Pagination
from collections import defaultdict
###############################
#from tornado.wsgi import WSGIContainer
#from tornado.httpserver import HTTPServer
#from tornado.ioloop import IOLoop
#from . import app

#http_server = HTTPServer(WSGIContainer(app))
#http_server.listen(5000)
#IOLoop.instance().start()
app = Flask(__name__)
app.config["APPLICATION_ROOT"]="/workflows"
app.config['PROPAGATE_EXCEPTIONS'] = True
app.use_reloader=True
CMO_CONFIG_LOC="/opt/common/CentOS_6-dev/cmo"
hello = __name__
lp = FlaskPad.from_file(CMO_CONFIG_LOC + "/cmo_launchpad.yaml")
PER_PAGE = 20
STATES = Firework.STATE_RANKS.keys()
client = MongoClient(host="pitchfork.mskcc.org", port=27017)
dbnames = client.database_names()

state_to_class= {"RUNNING" : "warning",
                 "WAITING" : "primary",
                 "FIZZLED" : "danger",
                 "READY"   : "info",
                 "COMPLETED" : "success",
                 "DEFUSED" : "default",
                 "RESERVED": "reserved"
                 }
state_to_color= {"RUNNING" : "#F4B90B",
                 "WAITING" : "#1F62A2",
                 "FIZZLED" : "#DB0051",
                 "READY"   : "#2E92F2",
                 "COMPLETED" : "#24C75A",
                 "DEFUSED": "#CCC",
                 "reserved": "#BB8BC1",
                 }


for administrative_db in ["admin", "local", "test", "daemons"]:
    if administrative_db in dbnames:
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

@app.route('/<dbname>/wf/<int:wf_id>/rerun')
def rerun_wf(dbname, wf_id):
    db_name  = dbname
    try:
        wf_id=int(wf_id)
    except:
        raise ValueError("Invalid wf_id: {}".format(wf_id))
    try:
        wf= lp.client[db_name].workflows.find({'nodes':wf_id}).next()
        for fw_id, state in wf['fw_states'].items():
            if state =="FIZZLED":
                print "attempting to rerun %s" % fw_id
                lp.rerun_fw(dbname, int(fw_id), rerun_duplicates=False)
        wf['state']="READY"
        #GET FW THAT IS FAILED OR DO NOTHING IF NO FAILED
        #call lp rerun fwi
    except:
        pass
    return redirect( db_name )

@app.route('/<dbname>/fw/<int:fw_id>/update', methods=['POST'])
def update_fw(dbname, fw_id):
    db_name = dbname
    global dbnames
    update_hash={}
    for (key, value) in request.form.items():
        update_hash[key]=value
#    if len(update_hash.keys())>0:
 #       print lp.client[db_name].fireworks.update({"fw_id":int(fw_id)},{"$set": {"spec._queueadapter":update_hash}})
    return redirect(os.path.join(db_name, "fw", str(fw_id) ))


@app.route('/<dbname>/fw/<int:fw_id>', methods=["GET"])
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
    file_pattern = os.path.join(fw['spec']['_launch_dir'], "*"+fw['launches'][0]['state_history'][0]['reservation_id'] + ".error")
    err_file = "Not Generated Yet!"
    err_out = ""
    out_file = "Not Generated Yet!"
    out_out = ""
    try :
        err_file = glob.glob(file_pattern)[0]
        err_out = "".join(tail(err_file, count=30))
        out_file = glob.glob(file_pattern.replace(".error", ".out"))[0]
        out_out = "".join(tail(out_file, count=30))
    except:
        pass
    return render_template('fw_details.html', **locals())

@app.route('/<dbname>/fw/<int:fw_id>/details')
def get_std(dbname, fw_id):
    fw = lp.get_fw_dict_by_id(dbname, fw_id)
    file_pattern = os.path.join(fw['spec']['_launch_dir'], "*.error")
    err_file = "Not Generated Yet!"
    err_out = ""
    out_file = "Not Generated Yet!"
    out_out = ""
    try :
        err_file = glob.glob(file_pattern)[0]
        err_out = "".join(tail(err_file, count=30))
        out_file = glob.glob(file_pattern.replace(".error", ".out"))[0]
        out_out = "".join(tail(out_file, count=30))
    except:
        pass
    return jsonify({"command": fw['spec']['_tasks'][0]['script'][0],"err_file":err_file, "out_file":out_file, "err_tail": err_out, "out_tail":out_out})

def tail(filename, count=1, offset=1024):
    """
    A more efficent way of getting the last few lines of a file.
    Depending on the length of your lines, you will want to modify offset
    to get better performance.
    """
    f_size = os.stat(filename).st_size
    if f_size == 0:
        return []
    with open(filename, 'r') as f:
        if f_size <= offset:
            offset = int(f_size / 2)
        while True:
            seek_to = min(f_size - offset, 0)
            f.seek(seek_to)
            lines = f.readlines()
            # Empty file
            if seek_to <= 0 and len(lines) == 0:
                return []
            # count is larger than lines in file
            if seek_to == 0 and len(lines) < count:
                return lines
            # Standard case
            if len(lines) >= (count + 1):
                return lines[count * -1:]


@app.route('/<dbname>/wf/<int:wf_id>')
def show_workflow(dbname, wf_id):
    global dbnames
    db_names = dbnames
    db_name = dbname
    try:
        int(wf_id)
    except ValueError:
        raise ValueError("Invalid fw_id: {}".format(wf_id))
    wf_projection = ['state', 'created_on', 'name', 'nodes']
    fw_proj = ['state','fw_id', 'name']
    wf = lp.client[db_name].workflows.find_one({'nodes':wf_id}, projection=wf_projection)
    q = {"fw_id" : {"$in": wf['nodes']}}
    try:
        if request.args.get('name') != None:
            name = request.args.get('name')
            q = {"$and": [ q, {"name": {"$regex":name}}]}
    except:
        pass
    rows = []
    for fw in lp.client[db_name].fireworks.find(q, projection=fw_proj):
        rows.append(fw)
    del wf["_id"]
    return render_template('wf_details.html', **locals())


@app.route('/<dbname>/json/<int:wf_id>')
def workflow_json(dbname, wf_id):
    global dbnames
    db_names = dbnames
    db_name = dbname
    try:
        int(wf_id)
    except ValueError:
        raise ValueError("Invalid fw_id: {}".format(wf_id))

    wf = lp.client[db_name].workflows.find_one({'nodes':wf_id})
    q = {"fw_id": {"$in":wf["nodes"]}}
    try:
        name =request.args.get('name')
        if name:
            q.update({"name": {"$regex": name}})
    except:
        pass
    fireworks = list(lp.client[dbname].fireworks.find(q, projection=["name","fw_id"]))
    node_name = dict()
    nodes_and_edges = { 'nodes': list(), 'edges': list()}
    for firework in fireworks:
        node_name[firework['fw_id']]=firework['name']
    if not name:
        #do full graph
        for node in wf['nodes']:
            node_obj = dict()
            node_obj['id'] = str(node)
            node_obj['name']=node_name[node]
            node_obj['state']=state_to_color[wf['fw_states'][str(node)]]
            node_obj['width']=len(node_obj['name'])*10
            nodes_and_edges['nodes'].append({'data':node_obj})
            if str(node) in wf['links']:
                for link in wf['links'][str(node)]:
                    link_object = dict()
                    link_object['source']=str(node)
                    link_object['target']=str(link)
                    nodes_and_edges['edges'].append({'data':link_object})
    else:
        for firework in fireworks:
            node_obj = dict()
            node_obj['id'] = str(firework['fw_id'])
            node_obj['name']=node_name[firework['fw_id']]
            node_obj['state']=state_to_color[wf['fw_states'][str(firework['fw_id'])]]
            node_obj['width']=len(node_obj['name'])*10
            nodes_and_edges['nodes'].append({'data':node_obj})

    return jsonify(nodes_and_edges)

@app.route('/<dbname>/fw/', defaults={"state": "total"})
@app.route("/<dbname>/fw/<state>/")
def fw_states(dbname, state):
    global dbnames
    db_names=dbnames
    db_name = dbname
    db = lp.client[dbname].fireworks
    q = {} if state == "total" else {"state": state}
    try:
        name =request.args.get('name')
        if name:
            q.update({"name": {"$regex": name}})
    except:
        pass
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
    db_names=dbnames
    db_name = dbname
    db = lp.client[dbname].workflows
    q = {} if state == "total" else {"state": state}
    try:
        name =request.args.get('name')
        if name:
            q.update({"name": {"$regex": name}})
    except:
        pass
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

@app.route("/", methods=['GET'])
@app.route("/<dbname>", methods=['GET'])
@app.route("/<dbname>/", methods=['GET'])
@app.route("/<dbname>/<page>", methods=['GET'])
def home(dbname=None, page=None):
    db_names = dbnames  
    fw_nums = []
    wf_nums = []
    selected = None
    if not dbname:
        return redirect("cmo")
    else:
        db_name = dbname
    for state in STATES:
        fw_nums.append(lp.get_fw_ids(dbname, query={'state': state}, count_only=True))
        wf_nums.append(lp.get_wf_ids(dbname,query={'state': state}, count_only=True))
    state_nums = zip(STATES, fw_nums, wf_nums)
    tot_fws = sum(fw_nums)
    tot_wfs = sum(wf_nums)
    num_pages = int(tot_wfs / PER_PAGE)
    if not page:
        page = 1
    else:
        page = int(page)
    #g Newest Workflows table data
    wfs_shown = lp.client[dbname].workflows.find({}, limit=PER_PAGE, sort=[('_id', DESCENDING)]).skip((page-1)*PER_PAGE)
    wf_info = []
    for item in wfs_shown:
        states = defaultdict(int)
        total = 0
        progress_bar_list = list()
        for key, value in item['fw_states'].items():
            states[value]+=1
            total+=1
        for state in ['RUNNING', 'READY', 'WAITING', 'COMPLETED', 'FIZZLED', "RESERVED"]:
            if state in states:
                progress_bar_list.append((state_to_class[state], states[state]*100/total, states[state]))
            
        wf_info.append({
            "id": item['nodes'][0],
            "name": item['name'],
            "state": item['state'],
            "progress_bar" : progress_bar_list,
            'panel_class' : state_to_class[item['state']]
        })

    return render_template('home.html', **locals())

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=9020)
