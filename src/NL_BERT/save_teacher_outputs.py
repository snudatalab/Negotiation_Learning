################################################################################
# Negotiation Learning
#
# Author: Ikhyun Cho (ikhyuncho@snu.ac.kr), Seoul National University
#         U Kang (ukang@snu.ac.kr), Seoul National University
#
# Version : 1.0
# Date : June 19, 2021
# Main Contact: Ikhyun Cho
#
# This software is free of charge under research purposes.
# For commercial purposes, please contact the authors.
#
################################################################################
"""
File used to save the teacher model's outputs to later on use for KD, PKD, PTP. Mainly based on [GitHub repository](https://github.com/intersun/PKD-for-BERT-Model-Compression) for [Patient Knowledge Distillation for BERT Model Compression](https://arxiv.org/abs/1908.09355).
"""

import pickle
import os
import glob
import logging
import argparse
import torch
import sys

import pandas as pd
import numpy as np
from torch.utils.data import SequentialSampler

# PROJECT_FOLDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# sys.path.append(PROJECT_FOLDER)

from utils import nli_data_processing
from envs import PROJECT_FOLDER, HOME_DATA_FOLDER, HOME_OUTPUT_FOLDER
from BERT.pytorch_pretrained_bert.modeling import BertConfig
from BERT.pytorch_pretrained_bert.tokenization import BertTokenizer
from utils.modeling import BertForSequenceClassificationEncoder, FCClassifierForSequenceClassification
from utils.utils import count_parameters, load_model_wonbon, eval_model_dataloader, eval_model_dataloader_nli,  fill_tensor, load_model
from utils.data_processing import init_model, get_task_dataloader_pretrain


logging.basicConfig(format='%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                    datefmt='%m/%d/%Y %H:%M:%S',
                    level=logging.INFO)
logger = logging.getLogger(__name__)


DEBUG = True

ALL_TASKS = ['MRPC', 'RTE', 'SST-2', 'MNLI', 'QQP', 'MNLI-mm', 'QNLI', 'race-merge', 'CoLA']

if DEBUG:
    # Specify the task in the line below.
    interested_task = 'MRPC'.split(',')
    prediction_mode_input = 'teacher:train,dev,test'
    output_all_layers = True   # True for patient teacher and False for normal teacher
    bert_model = 'bert-base-uncased'
    result_file = os.path.join(PROJECT_FOLDER, 'result/glue/result_summary/teacher_12layer_all.csv')
    

bert_model = os.path.join(HOME_DATA_FOLDER, f'models/pretrained/{bert_model}')
config = BertConfig(os.path.join(bert_model, 'bert_config.json'))
tokenizer = BertTokenizer.from_pretrained(bert_model, do_lower_case=True)
args = argparse.Namespace(n_gpu=1,
                          device=torch.device("cuda" if torch.cuda.is_available() else "cpu"),
                          fp16=False,
                          eval_batch_size=32,
                          max_seq_length=128)

prediction_mode = 'teacher'
output_dir = os.path.join(HOME_DATA_FOLDER, 'outputs/KD')
n_layer = 12

#specify the task.
task = 'MRPC'
#specify the directory of saved teacher model.
encoder_file = os.path.join(HOME_OUTPUT_FOLDER, 'MRPC/BERT_base_1/BERT.encoder_loss.pkl')
cls_file = os.path.join(HOME_OUTPUT_FOLDER, 'MRPC/BERT_base_1/BERT.cls_loss.pkl')

encoder_bert, classifier = init_model(task, output_all_layers, n_layer, config)
encoder_bert = load_model_wonbon(encoder_bert, encoder_file, args, 'exact', verbose=False)
classifier = load_model_wonbon(classifier, cls_file, args, 'exact', verbose=False)

args.raw_data_dir = os.path.join(HOME_DATA_FOLDER, 'data_raw', task)
#run_folder = os.path.join(KD_DIR, task, sub_dir, run_folder)



all_res = {'train': None, 'train_input_ids': None, 'train_labels': None, 'train_pred_answers': None, 'train_input_mask': None, 'train_segment_ids':None, 
               'dev': None, 'dev_input_ids': None, 'dev_labels': None, 'dev_pred_answers':None, 'dev_input_mask': None, 'dev_segment_ids' : None,
               'test': None, 'test_input_ids':None, 'test_labels': None,'test_pred_answers': None, 'test_input_mask': None, 'dev_segment_ids': None}
interested_set ={'dev', 'train', 'test'}
if 'dev' in interested_set or 'valid' in interested_set:
    dev_examples, dev_input_ids, dev_dataloader, dev_label_ids, dev_input_mask, dev_segment_ids = \
    get_task_dataloader_pretrain(task.lower(), 'dev', tokenizer, args, SequentialSampler, args.eval_batch_size)
    dev_res = eval_model_dataloader(encoder_bert, classifier, dev_dataloader, args.device, detailed=True, verbose=False)
    dev_pred_label = dev_res['pred_logit'].argmax(1)
    dev_label_ids_ = dev_label_ids
    dev_label_ids = (dev_label_ids.numpy() == dev_pred_label)  # (408,) true랑 false로 되어있음
    logger.info('for dev, acc = {}, loss = {}'.format(dev_res['acc'], dev_res['loss']))
    #logger.info('debug dev acc = {}'.format((dev_label_ids.numpy() == dev_pred_label).mean()))
    all_res['dev'] = dev_res
    all_res['dev_input_ids'] = dev_input_ids
    all_res['dev_pred_answers'] = dev_label_ids
    all_res['dev_input_mask'] = dev_input_mask
    all_res['dev_segment_ids'] = dev_segment_ids
    all_res['dev_labels'] = dev_label_ids_
        
if 'train' in interested_set:
    train_examples, train_input_ids, train_dataloader, train_label_ids, train_input_mask, train_segment_ids  = get_task_dataloader_pretrain(task.lower(), 'train', tokenizer, args, SequentialSampler, args.eval_batch_size)
    train_res = eval_model_dataloader(encoder_bert, classifier, train_dataloader, args.device, detailed=True, verbose=False)
    train_pred_label = train_res['pred_logit'].argmax(1)
    train_label_ids_ = train_label_ids
    train_label_ids = (train_label_ids.numpy() == train_pred_label)
    logger.info('for training, acc = {}, loss = {}'.format(train_res['acc'], train_res['loss']))
    #logger.info('debug train acc = {}'.format((train_label_ids.numpy() == train_pred_label).mean()))
    all_res['train'] = train_res
    all_res['train_input_ids'] = train_input_ids
    all_res['train_pred_answers'] = train_label_ids
    all_res['train_input_mask'] = train_input_mask
    all_res['train_segment_ids'] = train_segment_ids
    all_res['train_labels'] = train_label_ids_
        
# if 'test' in interested_set:
#     test_examples,test_input_ids, test_dataloader, test_label_ids, test_input_mask, test_segment_ids = get_task_dataloader_pretrain(task.lower(), 'test', tokenizer, args,SequentialSampler, args.eval_batch_size)
#     test_res = eval_model_dataloader(encoder_bert, classifier, test_dataloader, args.device, detailed=True, verbose=False)
#     test_pred_label = test_res['pred_logit'].argmax(1)
#     logger.info('for test, acc = {}, loss = {}'.format(test_res['acc'], test_res['loss']))
#     logger.info('debug test acc = {}'.format((test_label_ids.numpy() == test_pred_label).mean()))
#     test_label_ids_ = test_label_ids
#     test_label_ids = (test_label_ids.numpy() == test_pred_label)
#     if task == 'race-merge':
#         middle_id = np.array(['middle' in t.mrc_id for t in test_examples])
#         logger.info('race-middle test acc = {}'.format((test_label_ids.numpy()[middle_id] == test_pred_label[middle_id]).mean()))
#         logger.info('race-hight test acc = {}'.format((test_label_ids.numpy()[~middle_id] == test_pred_label[~middle_id]).mean()))
#     all_res['test'] = test_res
#     all_res['test_input_ids'] = test_input_ids
#     all_res['test_pred_answers'] = test_label_ids
#     all_res['test_input_mask'] = test_input_mask
#     all_res['test_segment_ids'] = test_segment_ids
#     all_res['test_labels'] = test_label_ids_
        
if prediction_mode in ['benchmark']:
    logger.info('saving benchmark results')
    processor = nli_data_processing.processors[task.lower()]()
    label_list = processor.get_labels()
    test_pred_label = [label_list[tr] for tr in test_res['pred_logit'].argmax(1)]
    test_pred = pd.DataFrame({'index': range(len(test_examples)), 'prediction': test_pred_label})
    if task == 'MNLI':
        test_pred.to_csv(os.path.join(output_dir, task + '-m.tsv'), sep='\t', index=False)
    else:
        test_pred.to_csv(os.path.join(output_dir, task + '.tsv'), sep='\t', index=False)
elif prediction_mode in ['teacher']:
    logger.info('saving teacher results')
    if not output_all_layers:
        #fname = os.path.join(output_dir, task, task + f'_distilbert_normal_kd_teacher_{n_layer}layer_result_summary.pkl')
        fname = os.path.join(output_dir, task, task + f'_Originalbert_base_pkd_normal_kd_teacher_{n_layer}layer_result_summary.pkl')
    else:
        #fname = os.path.join(output_dir, task, task + f'_distilbert_patient_kd_teacher_{n_layer}layer_result_summary.pkl')
        fname = os.path.join(output_dir, task, task + f'_Originalbert_base_patient_kd_teacher_{n_layer}layer_result_summary.pkl')
    with open(fname, 'wb') as fp:
        pickle.dump(all_res, fp)
logger.info(f'predicting for task {task} Done!')
