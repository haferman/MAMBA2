import os
import sys
sys.path.append(os.getcwd())
sys.path.append(sys.argv[1])
from programs.global_vars import *
from programs.create_db_helpers import *
from programs.match_helpers import *
from programs.logger_setup import *
from programs.general_helpers import *
logger=logger_setup('{}/{}'.format(CONFIG['projectPath'],CONFIG['log_file_name']))
import datetime as dt
import traceback

if __name__=='__main__':
    ###start up by printing out the full config file
    for key in CONFIG:
        logger.info('#############')
        logger.info('''{}: {}'''.format(key, CONFIG[key]))
    batch_summary={}
    ###set the start time
    batch_summary['batch_started'] = dt.datetime.now()
    batch_summary['batch_status'] = 'in_progress'
    batch_summary['batch_config'] = {}
    for key in CONFIG:
        if 'password' not in key:
            batch_summary['batch_config'][key]=CONFIG[key]
    batch_summary['batch_config']=json.dumps(batch_summary['batch_config'])
    ###check if the database exists already
    ##sqlite doesn't like multi-line statements
    #####IF database_creation mode is 'create', make the tables
    if CONFIG['database_creation_mode'].lower()=='create':
        if CONFIG['sql_flavor'] == 'sqlite':
            if os.path.isfile('{}/{}.db'.format(CONFIG['projectPath'],CONFIG['db_name']))==False:
                db=get_db_connection(CONFIG)
                cur = db.cursor()
                for stm in batch_info_qry.split(';'):
                    cur.execute(stm)
                db.commit()
        elif CONFIG['sql_flavor']=='postgres':
            test_conn = psycopg2.connect(host=CONFIG['db_host'],dbname='postgres',
                                  port=CONFIG['db_port'],
                                  user=CONFIG['db_user'],
                                  password=os.environ['db_password'],
                                  connect_timeout=10)
            cur=test_conn.cursor()
            cur.execute('''select 1 from pg_database where datname='{}' '''.format(CONFIG['db_name']))
            db_exists=cur.fetchall()
            if len(db_exists)==0:
                test_conn.set_isolation_level(0)
                cur.execute('''create database {} owner='{}' '''.format(CONFIG['db_name'], CONFIG['db_user']))
            test_conn.close()
            db=psycopg2.connect(host=CONFIG['db_host'],dbname=CONFIG['db_name'],
                                  port=CONFIG['db_port'],
                                  user=CONFIG['db_user'],
                                  password=os.environ['db_password'],
                                  connect_timeout=10)
            ###now create the schema
            cur=db.cursor()
            cur.execute('''create schema if not exists {} authorization {} '''.format(CONFIG['db_schema'], CONFIG['db_user']))
    db = get_db_connection(CONFIG)
    batch_summary['batch_id'] = CONFIG['batch_id'] = generate_batch_id(db)
    logger.info('#############')
    logger.info('''Batch ID: {}'''.format(CONFIG['batch_id']))
    ###update the batch summary
    update_batch_summary(batch_summary)
###create the database
    try:
        if CONFIG['database_creation_mode']=='create':
            createDatabase(CONFIG['db_name'])
    except Exception as error:
        logger.info('Error in creating database. Error: {}'.format(''.join(traceback.format_tb(error.__traceback__))))
        batch_summary['batch_status'] = 'failed'
        batch_summary['failure_message'] = 'Failed to create database tables.  See logs for information'
        update_batch_summary(batch_summary)
        os._exit(0)
    ####Either create or find our model
    try:
        if ast.literal_eval(CONFIG['prediction'])==True:
            training_data=pd.read_csv('{}/training_data_key.csv'.format(CONFIG['projectPath']), engine='c', dtype={'{}_id'.format(CONFIG['data1_name']):str,'{}_id'.format(CONFIG['data2_name']):str})
            if '.joblib' in CONFIG['saved_model']:
                mod = load_model(CONFIG, CONFIG['saved_model'])
            ###generate the rf_mod
            else:
                mod=choose_model(training_data)
                ###Dump it
                dump_model(mod, CONFIG)
        else:
            mod = {'model':'Just Candidates', 'variable_headers':'all'}
    except Exception as error:
        logger.info('Error in selecting . Error: {}'.format(''.join(traceback.format_tb(error.__traceback__))))
        batch_summary['batch_status'] = 'failed'
        batch_summary['failure_message'] = 'Failed to select a model.  See logs for information'
        update_batch_summary(batch_summary)
        os._exit(0)
    ###get the list of blocks
    try:
        ##run
        for block in blocks:
            logger.info('### Starting Block {}'.format(block['block_name']))
            run_block(block, mod, batch_summary['batch_id'])
            logger.info('### Completed Block {}'.format(block['block_name']))
    except Exception as error:
        logger.info('Error in running block {} . Error: {}'.format(block['block_name'],''.join(traceback.format_tb(error.__traceback__))))
        batch_summary['batch_status'] = 'failed'
        batch_summary['failure_message'] = 'Failed to run block {}.  See logs for information'.format(block['block_name'])
        update_batch_summary(batch_summary)
        os._exit(0)
    ###Once we are done, spit out the final information on the number of matches, and the .csv files
    ###check if output directory exists
    if os.path.isdir('{}/output/'.format(CONFIG['projectPath']))==False:
        logger.info('Creating Output Directory')
        os.makedirs('{}/output/'.format(CONFIG['projectPath']))
    db=get_db_connection(CONFIG)
    pd.DataFrame(get_table_noconn('''select * from matched_pairs where batch_id={} '''.format(batch_summary['batch_id']), db)).to_csv('{}/output/all_matches_batch_{}.csv'.format(CONFIG['projectPath'],batch_summary['batch_id']), index=False)
    pd.DataFrame(get_table_noconn('''select * from clerical_review_candidates where batch_id={} '''.format(batch_summary['batch_id']), db)).to_csv('{}/output/clerical_review_candidates_batch_{}.csv'.format(CONFIG['projectPath'],batch_summary['batch_id']), index=False)
    logger.info('Match Complete')
    if ast.literal_eval(CONFIG['prediction'])==True:
        summstats=get_table_noconn('''select count(distinct {}_id) data1_matched, count(distinct {}_id) data2_matched, count(*) total_pairs from matched_pairs'''.format(CONFIG['data1_name'], CONFIG['data2_name']), db)[0]
        logger.info('Matched {} records for {}'.format(summstats['data1_matched'], CONFIG['data1_name']))
        logger.info('Matched {} records for {}'.format(summstats['data2_matched'], CONFIG['data2_name']))
        logger.info('{} Total matched pairs'.format(summstats['total_pairs']))
        ####get the block summary data
        sumstats = get_table_noconn('''select block_level,
         count(block_matches) total_blocks,
         sum(block_size) total_pairs, 
         sum(block_time) total_time,
        sum(block_matches) total_matches, 
        sum(block_non_matches) total_non_matches,
        (sum(block_matches_avg_score * block_matches)/nullif(sum(block_matches),0)) average_match_score,
         (sum(block_non_matches_avg_score * block_non_matches)/nullif(sum(block_non_matches),0)) average_non_match_score
           from batch_statistics where batch_id = {} group by block_level'''.format(CONFIG['batch_id']),db)
        logger.info(sumstats)
    batch_summary['batch_status']='complete'
    batch_summary['batch_completed']=dt.datetime.now()
    update_batch_summary(batch_summary)
    db.close()
    os._exit(0)

