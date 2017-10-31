#!/usr/bin/python2

import requests
import requests.packages.urllib3

import json
from dateutil import parser
from datetime import datetime, timedelta
import multiprocessing
import os
import signal
import time
import Queue
from copy import deepcopy

from creds import TELEGRAM_API_TOKEN, SLACK_WEB_HOOK_URL

from random import randint

from model import Users, Notifications
import telepot

import logging
from logging.config import fileConfig

fileConfig('logging_config.ini')
logger = logging.getLogger()
logging.getLogger("requests").setLevel(logging.WARNING)
requests.packages.urllib3.disable_warnings()


# GLOBAL VARIABLE
PATH = os.path.dirname(os.path.abspath(__file__))
URL_1 = "https://www.airtel.in/account/AuthApp/CheckUser"
URL_2 = "https://www.airtel.in/pkmslogin.form"

TELEGRAM_MSG_TEMPLATE = """AIRTEL PREPAID BALANCE LOW
User = {name}, Number = {username}
Balance = {balance}
Threshold = {threshold}
"""

SLACK_MSG_TEMPLATE = """AIRTEL PREPAID BALANCE LOW
User = {name}, Number = {username}
Balance = {balance}
Threshold = {threshold}
"""

telegram_bot = telepot.Bot(
    os.environ.get('TELEGRAM_API_TOKEN', TELEGRAM_API_TOKEN))
# os.environ.get('SLACK_API_TOKEN', SLACK_API_TOKEN)


HTTP_TIMEOUT = 5


def user_initDB():
    try:
        data = list()
        for i in Users.select().where(Users.active == True).dicts():
            data.append(i)
    except Exception as e:
        logger.error('Failed to init user data', exc_info=True)
        exit(1)
    return data


# notifications
def slack_notif(NOTIF):
    try:
        msg = deepcopy(SLACK_MSG_TEMPLATE)
        slack_data = {'text': msg.format(**NOTIF)}
        response = requests.post(
            os.environ.get('SLACK_WEB_HOOK_URL', SLACK_WEB_HOOK_URL),
            data=json.dumps(slack_data),
            headers={'Content-Type': 'application/json'}
        )
        response.raise_for_status()
        NOTIF['updated'] = datetime.now()
    except Exception as e:
        logger.warn(
            'Sending notification failed\nReason: %s' % (e), exc_info=True)
    return NOTIF


def telegram_notif(NOTIF):
    try:
        msg = deepcopy(TELEGRAM_MSG_TEMPLATE)
        logger.debug(NOTIF)
        telegram_bot.sendMessage(NOTIF['n_id'], msg.format(**NOTIF))
        NOTIF['updated'] = datetime.now()
    except Exception as e:
        logger.warn(
            'Sending notification failed\nReason: %s' % (e), exc_info=True)

    return NOTIF


# worker data function
def do_stuff(job_queue, result_queue):
    logger.debug('Worker data process started')
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while not job_queue.empty():
        try:
            ACC = job_queue.get(block=True)
            result_queue.put(fetch_airtel(ACC))
        except Queue.Empty:
            pass
        except Exception as e:
            logger.error('Failed to get details for user', exc_info=True)


# worker notification function
def send_notification(notification_queue, notification_res_queue):
    logger.debug('Worker notification process started')
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while not notification_queue.empty():
        try:
            NOTIF = notification_queue.get(block=True)
            notification_res_queue.put(SendMessage(NOTIF))
        except Queue.Empty:
            pass
        except Exception as e:
            logger.error('Failed while sending notif', exc_info=True)


# Fetch Airtel data
def fetch_airtel(ACC):

    s = requests.Session()
    fetch_status = False

    RETRY_COUNT = 5
    while RETRY_COUNT > 0:

        logger.info("-->Trying for the %s time for user %s" % (
            6-RETRY_COUNT, ACC['name']))

        URL = "https://www.airtel.in/myaccount/Restservice/prepaid/account/balance/%(username)s" % (ACC)

        # STEP 1 : LOGIN
        headers = {"Cookie": "ssoLogin=userID=%s::loginType=Nomal::mobileNumber=%s::loginBy=password;" % (
            ACC['username'], ACC['username'])}
        values = {
            'password': '%(password)s' % (ACC),
            'login-form-type': 'pwd', 'username': '%(username)s' % (ACC)
        }
        try:
            time.sleep(randint(1, 4))
            r = s.post(
                URL_2, data=values, headers=headers, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            content = r.content
            if content.find('Authentication Fail') > 0:
                RETRY_COUNT = 0
                raise Exception('Authentication failed')

            if content.find('too many failed login') > 0:
                RETRY_COUNT = 0
                raise Exception('This account has been temporarily locked out due to too many failed login attempts.')

        except Exception as e:
            logger.warn(
                'Step 1 - Login Failed for user %s\nReason: %s' % (
                    ACC['name'], e), exc_info=False)
            RETRY_COUNT -= 1
            continue

        # STEP 2 : Getting balance
        headers = {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2272.101 Safari/537.36",
                    "AUTHUSER": "WCF0+FkIwd68Qv28GgzCoFWKE9PP+z/mEYrwvv/icjhV19Zr8l+JqL3V80KIMTHQ",
                    "LOGINID": "%(name)s" % (ACC),
                    "Accept": "application/json, text/javascript"
                  }
        try:
            time.sleep(randint(1, 4))
            r = s.get(URL, headers=headers, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            content = r.content
        except Exception as e:
            logger.warn('Step 2 - Fetching data for user %s\nReason: %s' % (
                ACC['name'], e), exc_info=False)
            RETRY_COUNT -= 1
            continue

        # STEP 3 : Decoding data
        try:
            resJ = json.loads(content)
            # successfully got data
            if resJ.has_key('dsStatusMessage') and resJ['dsStatusMessage'].lower().find('record successfully fetched') > -1:
                ACC['old_balance'] = ACC['balance']
                ACC['balance'] = float(resJ['businessOutput']['balance'])
                ACC['validity'] = parser.parse(resJ['businessOutput']['validity'])
                ACC['updated'] = datetime.now()
                fetch_status = True
                break
            else:
                logger.debug(content)
                raise Exception("Unable to get balance")
        except Exception as e:
            logger.debug(content)
            logger.warn('Step 3 - Error decoding JSON data for user %s\nReason: %s' % (ACC['name'], e), exc_info=True)
            RETRY_COUNT -= 1
            continue

    if not fetch_status:
        return dict()

    if ACC['balance'] <= ACC['threshold']:
        logger.info('Balance %(balance)s below threshold %(threshold)s for user %(name)s' % ACC)
    else:
        logger.info('Done for %(name)s, Balance %(balance)s, threshold %(threshold)s' % ACC)
    return ACC


def SendMessage(NOTIF):
    if NOTIF['n_type'] == 'telegram':
        return telegram_notif(NOTIF)

    if NOTIF['n_type'] == 'slack':
        return slack_notif(NOTIF)

    return NOTIF


# Main function
def main(args):
    job_queue = multiprocessing.Queue()
    result_queue = multiprocessing.Queue()
    notification_queue = multiprocessing.Queue()
    notification_res_queue = multiprocessing.Queue()

    NUM_PROCESSES = args.NUM_PROCESSES
    exit_ = False

    ACCOUNT_INFOS = user_initDB()

    '''
    AIRTEL DATA
    '''
    # Adding all work into job queue
    for ACCOUNT_INFO in ACCOUNT_INFOS:
        job_queue.put(ACCOUNT_INFO)
    logger.debug('Added %s user data to queue' % job_queue.qsize())

    workers = []
    # Starting processes
    for i in range(NUM_PROCESSES):
        process = multiprocessing.Process(
            target=do_stuff,
            args=(job_queue, result_queue))
        process.start()
        workers.append(process)
    logger.debug('Started processes of fetching data')

    # Starting work
    try:
        for worker in workers:
            worker.join()
    except KeyboardInterrupt:
        logger.warn('Killing all data processes, Reason : Ctrl+C')
        for worker in workers:
            worker.terminate()
            worker.join()
        exit_ = True
        logger.warn('Killed all data processes!')

    while not result_queue.empty():
        x = result_queue.get(block=True)
        try:
            tmp = dict()
            include = [
                'username', 'updated', 'balance',
                'old_balance', 'validity', 'active']
            tmp = {k: v for k, v in x.iteritems() if k in include}
            Users.update(**tmp).where(
                Users.username == tmp['username']).execute()
        except Exception as e:
            logger.error('Error updating user table', exc_info=True)

    if exit_:
        exit()

    '''
    NOTIFICATIONS
    '''
    yesterday = datetime.today()-timedelta(days=1)
    for i in Notifications.select(
        Notifications, Users.username, Users.name, Users.balance,
            Users.threshold
            ).join(Users).where(
                (Users.active == True) & (Notifications.active == True) & (
                    (Users.balance <= Users.threshold) & (
                        (Notifications.updated <= yesterday) |
                        (Users.old_balance > Users.balance)
                    )
                )
            ).dicts():
        logger.info('Sending notifications to %(name)s(%(username)s) via %(n_type)s' % (i))
        notification_queue.put(i)
    workers = []
    # Starting processes
    for i in range(NUM_PROCESSES):
        process = multiprocessing.Process(
            target=send_notification,
            args=(notification_queue, notification_res_queue))
        process.start()
        workers.append(process)
    logger.debug('Started notification processes')

    # Starting work
    try:
        for worker in workers:
            worker.join()
    except KeyboardInterrupt:
        logger.warn('Killing all notification processes, Reason : Ctrl+C')
        for worker in workers:
            worker.terminate()
            worker.join()
        logger.warn('Killed all notification processes!')

    while not notification_res_queue.empty():
        x = notification_res_queue.get(block=True)
        try:
            tmp = dict()
            include = ['updated']
            tmp = {k: v for k, v in x.iteritems() if k in include}
            Notifications.update(**tmp).where(
                Notifications.user == x['username']).execute()
        except Exception as e:
            logger.error('Error updating notification table', exc_info=True)

    time.sleep(1)


logger = logging.getLogger(__name__)
if __name__ == "__main__":
    import argparse
    __author__ = 'Advait'
    prsr = argparse.ArgumentParser(description='Script to check Airtel Mobile Prepaid Balance')
    prsr.add_argument('-p', '--process', help='Number of multiprocesses', type=int, required=False, default=1, dest='NUM_PROCESSES')
    args = prsr.parse_args()
    main(args)
