# coding: utf-8

from __future__ import unicode_literals
from datetime import datetime
from fireworks import Firework, Launch

"""
The LaunchPad manages the FireWorks database.
"""
import datetime
import json
import os
import random
import time
import traceback
from collections import OrderedDict, defaultdict

from pymongo import MongoClient
from pymongo import DESCENDING, ASCENDING

from fireworks.fw_config import LAUNCHPAD_LOC, SORT_FWS, \
    RESERVATION_EXPIRATION_SECS, RUN_EXPIRATION_SECS, MAINTAIN_INTERVAL, WFLOCK_EXPIRATION_SECS, \
    WFLOCK_EXPIRATION_KILL
from fireworks.utilities.fw_serializers import FWSerializable, reconstitute_dates
from fireworks.core.firework import Firework, Launch, Workflow, FWAction, Tracker
from fireworks.utilities.fw_utilities import get_fw_logger


__author__ = 'Anubhav Jain'
__copyright__ = 'Copyright 2013, The Materials Project'
__version__ = '0.1'
__maintainer__ = 'Anubhav Jain'
__email__ = 'ajain@lbl.gov'
__date__ = 'Jan 30, 2013'


# TODO: lots of duplication reduction and cleanup possible

class WFLock(object):
    """
    Lock a Workflow, i.e. for performing update operations
    """

    def __init__(self, lp, db_name, fw_id, expire_secs=WFLOCK_EXPIRATION_SECS, kill=WFLOCK_EXPIRATION_KILL):
        self.lp = lp
        self.db_name = db_name
        self.fw_id = fw_id
        self.expire_secs = expire_secs
        self.kill = kill

    def __enter__(self):
        ctr = 0
        waiting_time = 0
        links_dict = self.lp.client[self.db_name].workflows.find_and_modify({'nodes': self.fw_id, 'locked': {"$exists": False}},
                                                       {'$set': {'locked': True}})  # acquire lock
        while not links_dict:  # could not acquire lock b/c WF is already locked for writing
            ctr += 1
            time_incr = ctr/10.0+random.random()/100.0
            time.sleep(time_incr)  # wait a bit for lock to free up
            waiting_time += time_incr
            if waiting_time > self.expire_secs:  # too much time waiting, expire lock
                wf = self.lp.client[self.db_name].workflows.find_one({'nodes': self.fw_id})
                if not wf:
                    raise ValueError("Could not find workflow in database: {}".format(self.fw_id))
                if self.kill:  # force lock aquisition
                    self.lp.m_logger.warn('FORCIBLY ACQUIRING LOCK, WF: {}'.format(self.fw_id))
                    links_dict = self.lp.client[self.db_name].workflows.find_and_modify({'nodes': self.fw_id},
                                     {'$set': {'locked': True}})
                else:  # throw error if we don't want to force lock acquisition
                    raise ValueError("Could not get workflow - LOCKED: {}".format(self.fw_id))

            else:
                links_dict = self.lp.client[self.db_name].workflows.find_and_modify({'nodes': self.fw_id, 'locked': {"$exists":False}},
                                                           {'$set': {'locked': True}})  # retry lock

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.lp.client[self.db_name].workflows.find_and_modify({"nodes": self.fw_id}, {"$unset": {"locked": True}})


class FlaskPad(FWSerializable):
    """
    The LaunchPad manages the FireWorks database.
    """

    def __init__(self, host='localhost', port=27017, name='fireworks',
                 username=None, password=None, logdir=None, strm_lvl=None,
                 user_indices=None, wf_user_indices=None):
        """

        :param host:
        :param port:
        :param name:
        :param username:
        :param password:
        :param logdir:
        :param strm_lvl:
        :param user_indices:
        :param wf_user_indices:
        """
        self.host = host
        self.port = port
        self.name = name
        self.username = username
        self.password = password

        # set up logger
        self.logdir = logdir
        self.strm_lvl = strm_lvl if strm_lvl else 'INFO'
        self.m_logger = get_fw_logger('launchpad', l_dir=self.logdir,
                                      stream_level=self.strm_lvl)

        self.user_indices = user_indices if user_indices else []
        self.wf_user_indices = wf_user_indices if wf_user_indices else []

        # get connection
        self.client = MongoClient(host, port, j=True)
    
    def to_dict(self):
        return self.full_fw.to_dict()

    @classmethod
    def from_dict(cls, d):
        logdir = d.get('logdir', None)
        strm_lvl = d.get('strm_lvl', None)
        user_indices = d.get('user_indices', [])
        wf_user_indices = d.get('wf_user_indices', [])
        return FlaskPad(d['host'], d['port'], d['name'], d['username'],
                         d['password'], logdir, strm_lvl, user_indices,
                         wf_user_indices)


    def get_fw_dict_by_id(self, dbname, fw_id):
        fw_dict = self.client[dbname].fireworks.find_one({'fw_id': fw_id})

        if not fw_dict:
            raise ValueError('No Firework exists with id: {}'.format(fw_id))
            # recreate launches from the launch collection

        fw_dict['launches'] = list(self.client[dbname].launches.find(
                    {'launch_id': {"$in": fw_dict['launches']}}))

        fw_dict['archived_launches'] = list(self.client[dbname].launches.find(
                    {'launch_id': {"$in": fw_dict['archived_launches']}}))
        return fw_dict

    def get_fw_by_id(self, dbname, fw_id):
        """
        Given a Firework id, give back a Firework object

        :param fw_id: Firework id (int)
        :return: Firework object
        """
        return Firework.from_dict(self.get_fw_dict_by_id(fw_id))

    def get_wf_by_fw_id(self, dbname, fw_id):
        """
        Given a Firework id, give back the Workflow containing that Firework
        :param fw_id:
        :return: A Workflow object
        """
        links_dict = self.client[dbname].workflows.find_one({'nodes': fw_id})
        if not links_dict:
            raise ValueError("Could not find a Workflow with fw_id: {}".format(fw_id))
        fws = map(self.get_fw_by_id, links_dict["nodes"])
        return Workflow(fws, links_dict['links'], links_dict['name'],
                        links_dict['metadata'], links_dict['created_on'], links_dict['updated_on'])

    def delete_wf(self, dbname, fw_id):
        links_dict = self.client[dbname].workflows.find_one({'nodes': fw_id})
        fw_ids = links_dict["nodes"]
        potential_launch_ids = []
        launch_ids = []
        for i in fw_ids:
            fw_dict = self.client[dbname].fireworks.find_one({'fw_id': i})
            potential_launch_ids += fw_dict["launches"] + fw_dict['archived_launches']

        for i in potential_launch_ids:  # only remove launches if no ohter fws refer to them
            if not self.client[dbname].fireworks.find_one({'$or': [{"launches": i}, {'archived_launches': i}],
                                            'fw_id': {"$nin": fw_ids}}, {'launch_id': 1}):
                launch_ids.append(i)

        print("Remove fws %s" % fw_ids)
        print("Remove launches %s" % launch_ids)
        print("Removing workflow.")
        self.client[dbname].launches.remove({'launch_id': {"$in": launch_ids}})
        self.client[dbname].fireworks.remove({"fw_id": {"$in": fw_ids}})
        self.client[dbname].workflows.remove({'nodes': fw_id})

    def get_wf_summary_dict(self, dbname, fw_id, mode="more"):
        """
        A much faster way to get summary information about a Workflow by
        querying only for needed information.

        Args:
            fw_id (int): A Firework id.
            mode (str): Choose between "more", "less" and "all" in terms of
                quantity of information.

        Returns:
            (dict) of information about Workflow.
        """
        wf_fields = ["state", "created_on", "name", "nodes"]
        fw_fields = ["state", "fw_id"]
        launch_fields = []

        if mode != "less":
            wf_fields.append("updated_on")
            fw_fields.extend(["name", "launches"])
            launch_fields.append("launch_id")
            launch_fields.append("launch_dir")

        if mode == "reservations":
            launch_fields.append("state_history.reservation_id")

        if mode == "all":
            wf_fields = None

        wf = self.client[dbname].workflows.find_one({"nodes": fw_id}, projection=wf_fields)
        fw_data = []
        id_name_map = {}
        launch_ids = []
        for fw in self.client[dbname].fireworks.find({"fw_id": {"$in": wf["nodes"]}},
                                      projection=fw_fields):
            if launch_fields:
                launch_ids.extend(fw["launches"])
            fw_data.append(fw)
            if mode != "less":
                id_name_map[fw["fw_id"]] = "%s--%d" % (fw["name"], fw["fw_id"])

        if launch_fields:
            launch_info = defaultdict(list)
            for l in self.client[dbname].launches.find({'launch_id': {"$in": launch_ids}},
                                        projection=launch_fields):
                for i, fw in enumerate(fw_data):
                    if l["launch_id"] in fw["launches"]:
                        launch_info[i].append(l)
            for k, v in launch_info.items():
                fw_data[k]["launches"] = v

        wf["fw"] = fw_data

        # Post process the summary dict so that it "looks" better.
        if mode == "less":
            wf["states_list"] = "-".join(
                [fw["state"][:3] if fw["state"].startswith("R")
                 else fw["state"][0] for fw in wf["fw"]])
            del wf["nodes"]
        elif mode == "more":
            wf["states"] = OrderedDict()
            wf["launch_dirs"] = OrderedDict()
            for fw in wf["fw"]:
                k = "%s--%d" % (fw["name"], fw["fw_id"])
                wf["states"][k] = fw["state"]
                wf["launch_dirs"][k] = [l["launch_dir"] for l in fw[
                    "launches"]]
            del wf["nodes"]
        elif mode == "all":
            wf["links"] = {id_name_map[int(k)]: [id_name_map[i] for i in v]
                           for k, v in wf["links"].items()}
            wf["nodes"] = map(id_name_map.get, wf["nodes"])
            wf["parent_links"] = {
                id_name_map[int(k)]: [id_name_map[i] for i in v]
                for k, v in wf["parent_links"].items()}
        elif mode == "reservations":
            wf["states"] = OrderedDict()
            wf["launches"] = OrderedDict()
            for fw in wf["fw"]:
                k = "%s--%d" % (fw["name"], fw["fw_id"])
                wf["states"][k] = fw["state"]
                wf["launches"][k] = fw["launches"]
            del wf["nodes"]

        del wf["_id"]
        del wf["fw"]

        return wf

    def get_fw_ids(self, dbname, query=None, sort=None, limit=0, count_only=False):
        """
        Return all the fw ids that match a query,
        :param query: (dict) representing a Mongo query
        :param sort: [(str,str)] sort argument in Pymongo format
        :param limit: (int) limit the results
        :param count_only: (bool) only return the count rather than explicit ids
        """
        fw_ids = []
        criteria = query if query else {}

        if count_only:
            if limit:
                return ValueError("Cannot count_only and limit at the same time!")
            return self.client[dbname].fireworks.find(criteria, {}, sort=sort).count()

        for fw in self.client[dbname].fireworks.find(criteria, {"fw_id": True}, sort=sort).limit(limit):
            fw_ids.append(fw["fw_id"])
        return fw_ids

    def get_wf_ids(self, dbname, query=None, sort=None, limit=0, count_only=False):
        """
        Return one fw id for all workflows that match a query,
        :param query: (dict) representing a Mongo query
        :param sort: [(str,str)] sort argument in Pymongo format
        :param limit: (int) limit the results
        :param count_only: (bool) only return the count rather than explicit ids
        """
        wf_ids = []
        criteria = query if query else {}
        if count_only:
            return self.client[dbname].workflows.find(criteria, {"nodes": True}, sort=sort).limit(limit).count()

        for fw in self.client[dbname].workflows.find(criteria, {"nodes": True}, sort=sort).limit(limit):
            wf_ids.append(fw["nodes"][0])

        return wf_ids

    def rerun_fw(self, db_name, fw_id, rerun_duplicates=True):
        m_fw = self.client[db_name].fireworks.find_one({"fw_id": fw_id}, {"state": 1})
        print m_fw
        # detect FWs that share the same launch. Must do this before rerun
        duplicates = []
        reruns = []
        if rerun_duplicates:
            f = self.client[db_name].fireworks.find_one({"fw_id": fw_id, "spec._dupefinder": {"$exists": True}}, {'launches':1})
            if f:
                for d in self.client[db_name].fireworks.find({"launches": {"$in": f['launches']}, "fw_id": {"$ne": fw_id}}, {"fw_id": 1}):
                    duplicates.append(d['fw_id'])
            duplicates = list(set(duplicates))
        # rerun this FW
        if m_fw['state'] in ['ARCHIVED', 'DEFUSED'] :
            self.m_logger.info("Cannot rerun fw_id: {}: it is {}.".format(fw_id, m_fw['state']))
        elif m_fw['state'] == 'WAITING':
            self.m_logger.debug("Skipping rerun fw_id: {}: it is already WAITING.".format(fw_id))
        else:
            print "got here"
            with WFLock(self, db_name, fw_id):
                print "and into the lock"
                wf = self.get_wf_by_fw_id_lzyfw(db_name, fw_id)
                print wf
                updated_ids = wf.rerun_fw(fw_id)
                print updated_ids
                self._update_wf(db_name, wf, updated_ids)
                reruns.append(fw_id)

        # rerun duplicated FWs
        for f in duplicates:
            self.m_logger.info("Also rerunning duplicate fw_id: {}".format(f))
            r = self.rerun_fw(db_name, f, rerun_duplicates=False)  # False for speed, True shouldn't be needed
            reruns.extend(r)
        print reruns
        return reruns  # return the ids that were rerun

    def _update_wf(self, db_name, wf, updated_ids):
        # note: must be called within an enclosing WFLock

        updated_fws = [wf.id_fw[fid] for fid in updated_ids]
        old_new = self._upsert_fws(db_name, updated_fws)
        wf._reassign_ids(old_new)

        # find a node for which the id did not change, so we can query on it to get WF
        query_node = None
        for f in wf.id_fw:
            if f not in old_new.values() or old_new.get(f, None) == f:
                query_node = f
                break

        assert query_node is not None
        if not self.client[db_name].workflows.find_one({'nodes': query_node}):
            raise ValueError("BAD QUERY_NODE! {}".format(query_node))
        # redo the links
        wf = wf.to_db_dict()
        wf['locked'] = True  # preserve the lock!
        self.client[db_name].workflows.find_and_modify({'nodes': query_node}, wf)

    def _upsert_fws(self, db_name, fws, reassign_all=False):
        old_new = {} # mapping between old and new Firework ids

        # sort the FWs by id, then the new FW_ids will match the order of the old ones...
        fws.sort(key=lambda x: x.fw_id)

        for fw in fws:
            if fw.fw_id < 0 or reassign_all:
                new_id = self.get_new_fw_id(db_name)
                old_new[fw.fw_id] = new_id
                fw.fw_id = new_id
            self.client[db_name].fireworks.find_and_modify({'fw_id': fw.fw_id}, fw.to_db_dict(), upsert=True)

        return old_new


    def get_new_fw_id(self, db_name):
        """
        Checkout the next Firework id
        """
        try:
            return self.client[db_name].fw_id_assigner.find_and_modify({}, {'$inc': {'next_fw_id': 1}})['next_fw_id']
        except:
            raise ValueError("Could not get next FW id! If you have not yet initialized the database, please do so by performing a database reset (e.g., lpad reset)")

    def get_wf_by_fw_id_lzyfw(self, dbname, fw_id):
        """
        Given a FireWork id, give back the Workflow containing that FireWork
        :param fw_id:
        :return: A Workflow object
        """
        links_dict = self.client[dbname].workflows.find_one({'nodes': fw_id})
        if not links_dict:
            raise ValueError("Could not find a Workflow with fw_id: {}".format(fw_id))

        fws = []
        for fw_id in links_dict['nodes']:
            fws.append(LazyFirework(fw_id, self.client[dbname].fireworks, self.client[dbname].launches))
        # Check for fw_states in links_dict to conform with preoptimized workflows
        if 'fw_states' in links_dict:
            fw_states = dict([(int(k), v) for (k, v) in links_dict['fw_states'].items()])
        else:
            fw_states = None

        return Workflow(fws, links_dict['links'], links_dict['name'],
                        links_dict['metadata'], links_dict['created_on'],
                        links_dict['updated_on'], fw_states)

class LazyFirework(object):
    """
    A LazyFirework only has the fw_id, and grabs other data just-in-time.
    This representation can speed up Workflow loading as only "important" FWs need to be
    fully loaded.
    :param fw_id:
    :param fw_coll:
    :param launch_coll:
    """

    # Get these fields from DB when creating new FireWork object
    db_fields = ('name', 'fw_id', 'spec', 'created_on', 'state')
    db_launch_fields = ('launches', 'archived_launches')

    def __init__(self, fw_id, fw_coll, launch_coll):
        # This is the only attribute known w/o a DB query
        self.fw_id = fw_id

        self._fwc, self._lc = fw_coll, launch_coll
        self._launches = {k: False for k in self.db_launch_fields}
        self._fw, self._lids = None, None

    # FireWork methods

    @property
    def state(self):
        return self.partial_fw._state

    @state.setter
    def state(self, state):
        self.partial_fw._state = state
        self.partial_fw.updated_on = datetime.datetime.utcnow()

    def to_dict(self):
        return self.full_fw.to_dict()

    def _rerun(self):
        self.full_fw._rerun()

    def to_db_dict(self):
        return self.full_fw.to_db_dict()

    def __str__(self):
        return 'LazyFireWork object: (id: {})'.format(self.fw_id)

    # Properties that shadow FireWork attributes

    @property
    def tasks(self): return self.partial_fw.tasks
    @tasks.setter
    def tasks(self, value): self.partial_fw.tasks = value

    @property
    def spec(self): return self.partial_fw.spec
    @spec.setter
    def spec(self, value): self.partial_fw.spec = value

    @property
    def name(self): return self.partial_fw.name
    @name.setter
    def name(self, value): self.partial_fw.name = value

    @property
    def created_on(self): return self.partial_fw.created_on
    @created_on.setter
    def created_on(self, value): self.partial_fw.created_on = value

    @property
    def updated_on(self): return self.partial_fw.updated_on
    @updated_on.setter
    def updated_on(self, value): self.partial_fw.updated_on = value

    @property
    def parents(self): return self.partial_fw.parents
    @parents.setter
    def parents(self, value): self.partial_fw.parents = value

    # Properties that shadow FireWork attributes, but which are
    # fetched individually from the DB (i.e. launch objects)

    @property
    def launches(self):
        return self._get_launch_data('launches')
    @launches.setter
    def launches(self, value):
        self._launches['launches'] = True
        self.partial_fw.launches = value

    @property
    def archived_launches(self):
        return self._get_launch_data('archived_launches')
    @archived_launches.setter
    def archived_launches(self, value):
        self._launches['archived_launches'] = True
        self.partial_fw.archived_launches = value

    # Lazy properties that idempotently instantiate a FireWork object

    @property
    def partial_fw(self):
        if not self._fw:
            fields = list(self.db_fields) + list(self.db_launch_fields)
            data = self._fwc.find_one({'fw_id': self.fw_id}, projection=fields)
            launch_data = {}  # move some data to separate launch dict
            for key in self.db_launch_fields:
                launch_data[key] = data[key]
                del data[key]

            self._lids = launch_data
            self._fw = Firework.from_dict(data)
        return self._fw

    @property
    def full_fw(self):
        #map(self._get_launch_data, self.db_launch_fields)
        for launch_field in self.db_launch_fields:
            self._get_launch_data(launch_field)
        return self._fw

    # Get a type of Launch object

    def _get_launch_data(self, name):
        """Pull launch data individually for each field.

        :param name: Name of field, e.g. 'archived_launches'.
        :return: Launch obj (also propagated to self._fw)
        """
        fw = self.partial_fw  # assure stage 1
        if not self._launches[name]:
            launch_ids = self._lids[name]
            if launch_ids:
                data = self._lc.find({'launch_id': {"$in": launch_ids}})
                result = list(map(Launch.from_dict, data))
            else:
                result = []
            setattr(fw, name, result)  # put into real FireWork obj
            self._launches[name] = True
        return getattr(fw, name)
