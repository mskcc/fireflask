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

    def __init__(self, lp, fw_id, expire_secs=WFLOCK_EXPIRATION_SECS, kill=WFLOCK_EXPIRATION_KILL):
        self.lp = lp
        self.fw_id = fw_id
        self.expire_secs = expire_secs
        self.kill = kill

    def __enter__(self):
        ctr = 0
        waiting_time = 0
        links_dict = self.lp.workflows.find_and_modify({'nodes': self.fw_id, 'locked': {"$exists": False}},
                                                       {'$set': {'locked': True}})  # acquire lock
        while not links_dict:  # could not acquire lock b/c WF is already locked for writing
            ctr += 1
            time_incr = ctr/10.0+random.random()/100.0
            time.sleep(time_incr)  # wait a bit for lock to free up
            waiting_time += time_incr
            if waiting_time > self.expire_secs:  # too much time waiting, expire lock
                wf = self.lp.workflows.find_one({'nodes': self.fw_id})
                if not wf:
                    raise ValueError("Could not find workflow in database: {}".format(self.fw_id))
                if self.kill:  # force lock aquisition
                    self.lp.m_logger.warn('FORCIBLY ACQUIRING LOCK, WF: {}'.format(self.fw_id))
                    links_dict = self.lp.workflows.find_and_modify({'nodes': self.fw_id},
                                     {'$set': {'locked': True}})
                else:  # throw error if we don't want to force lock acquisition
                    raise ValueError("Could not get workflow - LOCKED: {}".format(self.fw_id))

            else:
                links_dict = self.lp.workflows.find_and_modify({'nodes': self.fw_id, 'locked': {"$exists":False}},
                                                           {'$set': {'locked': True}})  # retry lock

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.lp.workflows.find_and_modify({"nodes": self.fw_id}, {"$unset": {"locked": True}})


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
