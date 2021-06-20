"""
The main file used to train student and teacher models. Mainly based on [GitHub repository](https://github.com/intersun/PKD-for-BERT-Model-Compression) for [Patient Knowledge Distillation for BERT Model Compression](https://arxiv.org/abs/1908.09355).
"""

import logging
import os
import random
import pickle

import numpy as np
import torch
from torch.utils.data import RandomSampler, SequentialSampler
from tqdm import tqdm, trange
import torch.nn as nn
from BERT.pytorch_pretrained_bert.modeling import BertConfig
from BERT.pytorch_pretrained_bert.optimization import BertAdam, warmup_linear
from BERT.pytorch_pretrained_bert.tokenization import BertTokenizer
from BERT.pytorch_pretrained_bert.quantization_modules import calculate_next_quantization_parts
from utils.argument_parser import default_parser, get_predefine_argv, complete_argument
from utils.nli_data_processing import processors, output_modes
from utils.data_processing import get_task_dataloader, init_model_NL
from utils.modeling import BertForSequenceClassificationEncoder, FCClassifierForSequenceClassification, FullFCClassifierForSequenceClassification
from utils.utils import load_model, count_parameters, eval_model_dataloader_nli_NL, eval_model_dataloader, compute_metrics, load_model_NL
from utils.KD_loss import distillation_loss, patience_loss
from envs import HOME_DATA_FOLDER
from BERT.pytorch_pretrained_bert.quantization_modules import quantization

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                    datefmt='%m/%d/%Y %H:%M:%S',
                    level=logging.INFO)
logger = logging.getLogger(__name__)


#########################################################################
# Prepare Parser
##########################################################################
parser = default_parser()
DEBUG = True
logger.info("IN CMD MODE")
args = parser.parse_args()

# The code might not be that clean in the current version.

train_seed_fixed = args.train_seed
saving_criterion_acc_fixed = args.saving_criterion_acc
saving_criterion_loss_fixed = args.saving_criterion_loss
train_batch_size_fixed = args.train_batch_size
eval_batch_size_fixed = args.eval_batch_size
model_type_fixed = args.model_type
save_model_dir_fixed = args.save_model_dir
output_dir_fixed = args.output_dir
load_model_dir_fixed = args.load_model_dir
layer_initialization_fixed = args.layer_initialization
teacher_prediction_fixed = args.teacher_prediction

# Note that args.NL_mode = 2 is equivalent to Dual Learning
NL_mode_fixed = args.NL_mode

#teacher_num = args.teacher_numb
task_name_fixed = args.task
if DEBUG:
    logger.info("IN DEBUG MODE")
    
    argv = get_predefine_argv(args, 'glue', args.task, args.train_type, args.student_hidden_layers)
    
    try:
        args = parser.parse_args(argv)
    except NameError:
        raise ValueError('please uncomment one of option above to start training')
else:
    logger.info("IN CMD MODE")
    args = parser.parse_args()

args.output_dir = output_dir_fixed
if load_model_dir_fixed is not None:
    args.load_model_dir = load_model_dir_fixed
args = complete_argument(args, args.output_dir, args.load_model_dir)

    
if train_seed_fixed is not None:
    args.train_seed = train_seed_fixed
if saving_criterion_acc_fixed is not None:
    args.saving_criterion_acc = saving_criterion_acc_fixed
if saving_criterion_loss_fixed is not None:
    args.saving_criterion_loss = saving_criterion_loss_fixed
if train_batch_size_fixed is not None:
    args.train_batch_size = train_batch_size_fixed
if eval_batch_size_fixed is not None:
    args.eval_batch_size = eval_batch_size_fixed
if save_model_dir_fixed is not None:
    args.save_model_dir = save_model_dir_fixed
if args.load_model_dir is not None:
    args.encoder_checkpoint = args.load_model_dir
if task_name_fixed is not None:
    args.task_name = task_name_fixed
    args.task = task_name_fixed
if layer_initialization_fixed is not None:
    args.layer_initialization = layer_initialization_fixed
if NL_mode_fixed is not None:
    args.NL_mode = NL_mode_fixed
if teacher_prediction_fixed is not None:
    args.teacher_prediction = teacher_prediction_fixed
    
args.model_type = model_type_fixed
args.raw_data_dir = os.path.join(HOME_DATA_FOLDER, 'data_raw', args.task_name)
args.feat_data_dir = os.path.join(HOME_DATA_FOLDER, 'data_feat', args.task_name)

args.train_batch_size = args.train_batch_size // args.gradient_accumulation_steps
logger.info('actual batch size on all GPU = %d' % args.train_batch_size)
device, n_gpu = args.device, args.n_gpu

###################################################################################################################################
    
random.seed(args.train_seed)
np.random.seed(args.train_seed)
torch.manual_seed(args.train_seed)
if args.n_gpu > 0:
    torch.cuda.manual_seed_all(args.train_seed)

    if args.model_type == 'NL':
        if args.student_hidden_layers == 3:
            args.fc_layer_idx = '1,3,5,7,9' # for original BERT
            #args.fc_layer_idx = '0,1,2,3,4' # for TinyBERT and DistilBERT
        elif args.student_hidden_layers == 6:
            args.fc_layer_idx = '0,1,2,3,4,5,6,7,8,9,10'    
        
logger.info('Input Argument Information')
args_dict = vars(args)
for a in args_dict:
    logger.info('%-28s  %s' % (a, args_dict[a]))
    
    
#########################################################################
# Prepare  Data
##########################################################################
task_name = args.task_name.lower()

if task_name not in processors and 'race' not in task_name:
    raise ValueError("Task not found: %s" % (task_name))

if 'race' in task_name:
    pass
else:
    processor = processors[task_name]()
    output_mode = output_modes[task_name]

    label_list = processor.get_labels()
    num_labels = len(label_list)

tokenizer = BertTokenizer.from_pretrained(args.bert_model, do_lower_case=True)

if args.do_train:
    train_sampler = SequentialSampler if DEBUG else RandomSampler
    read_set = 'train'
    if args.teacher_prediction is not None and args.alpha > 0:
        logger.info('loading teacher\'s prediction')
        teacher_predictions = pickle.load(open(args.teacher_prediction, 'rb'))['train'] if args.teacher_prediction is not None else None
        #teacher_predictions = pickle.load(open(args.real_teacher, 'rb'))['train'] if args.real_teacher is not None else logger.info("shibal")
        
        logger.info('teacher acc = %.2f, teacher loss = %.5f' % (teacher_predictions['acc']*100, teacher_predictions['loss']))
        
        teacher_predictions_ = pickle.load(open(args.teacher_prediction, 'rb'))['dev'] if args.teacher_prediction is not None else None
        #teacher_predictions_ = pickle.load(open(args.real_teacher, 'rb'))['dev'] if args.real_teacher is not None else None
        
        logger.info('teacher acc = %.2f, teacher loss = %.5f' % (teacher_predictions_['acc']*100, teacher_predictions_['loss']))
        
        
        if args.kd_model == 'kd':
            train_examples, train_dataloader, _ = get_task_dataloader(task_name, read_set, tokenizer, args, SequentialSampler,
                                                                      batch_size=args.train_batch_size,
                                                                      knowledge=teacher_predictions['pred_logit'])
        else:
            train_examples, train_dataloader, _ = get_task_dataloader(task_name, read_set, tokenizer, args, SequentialSampler,
                                                                      batch_size=args.train_batch_size,
                                                                      knowledge=teacher_predictions['pred_logit'],
                                                                      extra_knowledge=teacher_predictions['feature_maps'])

    else:
        if args.alpha > 0:
            raise ValueError('please specify teacher\'s prediction file for KD training')
        logger.info('runing simple fine-tuning because teacher\'s prediction is not provided')
        train_examples, train_dataloader, _ = get_task_dataloader(task_name, read_set, tokenizer, args, SequentialSampler,
                                                                  batch_size=args.train_batch_size)
    num_train_optimization_steps = int(len(train_examples) / args.train_batch_size / args.gradient_accumulation_steps) * args.num_train_epochs
    logger.info("***** Running training *****")
    logger.info("  Num examples = %d", len(train_examples))
    logger.info("  Batch size = %d", args.train_batch_size)
    logger.info("  Num steps = %d", num_train_optimization_steps)

    # Run prediction for full data
    eval_examples, eval_dataloader, eval_label_ids = get_task_dataloader(task_name, 'dev', tokenizer, args, SequentialSampler, batch_size=args.eval_batch_size)
    logger.info("***** Running evaluation *****")
    logger.info("  Num examples = %d", len(eval_examples))
    logger.info("  Batch size = %d", args.eval_batch_size)

# if args.do_eval:
#     test_examples, test_dataloader, test_label_ids = get_task_dataloader(task_name, 'test', tokenizer, args, SequentialSampler, batch_size=args.eval_batch_size)
#     logger.info("***** Running evaluation *****")
#     logger.info("  Num examples = %d", len(test_examples))
#     logger.info("  Batch size = %d", args.eval_batch_size)


#########################################################################
# Prepare model
#########################################################################
student_config = BertConfig(os.path.join(args.bert_model, 'bert_config.json'))
if args.kd_model.lower() in ['kd', 'kd.cls', 'kd.u', 'kd.i']:
    logger.info('using normal Knowledge Distillation')
    output_all_layers = (args.kd_model.lower() in ['kd.cls', 'kd.u', 'kd.i'])
    
    # if original model
    if args.model_type == 'NL':
        student_encoder, student_classifier, student_classifier_2, student_classifier_3 = init_model_NL(task_name, output_all_layers, args.student_hidden_layers, student_config)
        
    n_student_layer = len(student_encoder.bert.encoder.layer)
    layer_initialization = args.layer_initialization.split(',')
    for i in range(len(layer_initialization)):
        layer_initialization[i] = int(layer_initialization[i])

    student_encoder = load_model_NL(student_encoder, layer_initialization, args.encoder_checkpoint, args, 'student', verbose= True)
    logger.info('*' * 77)
    student_classifier = load_model(student_classifier, args.cls_checkpoint, args, 'classifier', verbose= True)
    student_classifier_2 = load_model(student_classifier_2, args.cls_checkpoint, args, 'classifier', verbose= True)
    student_classifier_3 = load_model(student_classifier_3, args.cls_checkpoint, args, 'classifier', verbose= True)
    
elif args.kd_model.lower() == 'kd.full':
    logger.info('using FULL Knowledge Distillation')
    layer_idx = [int(i) for i in args.fc_layer_idx.split(',')]
    num_fc_layer = len(layer_idx)
    if args.weights is None or args.weights.lower() in ['none']:
        weights = np.array([1] * (num_fc_layer-1) + [num_fc_layer-1]) / 2 / (num_fc_layer-1)
    else:
        weights = [float(w) for w in args.weights.split(',')]
        weights = np.array(weights) / sum(weights)

    assert len(weights) == num_fc_layer, 'number of weights and number of FC layer must be equal to each other'

    # weights = torch.tensor(np.array([1, 1, 1, 1, 2, 6])/12, dtype=torch.float, device=device, requires_grad=False)
    # if args.fp16:
    #    weights = weights.half()
    student_encoder = BertForSequenceClassificationEncoder(student_config, output_all_encoded_layers=True,
                                                           num_hidden_layers=args.student_hidden_layers,
                                                           fix_pooler=True)
    n_student_layer = len(student_encoder.bert.encoder.layer)
    student_encoder = load_model(student_encoder, args.encoder_checkpoint, args, 'student', verbose=True)
    logger.info('*' * 77)

    student_classifier = FullFCClassifierForSequenceClassification(student_config, num_labels, student_config.hidden_size,
                                                                   student_config.hidden_size, 6)
    student_classifier = load_model(student_classifier, args.cls_checkpoint, args, 'exact', verbose=True)
    assert max(layer_idx) <= n_student_layer - 1, 'selected FC layer idx cannot exceed the number of transformers'
else:
    raise ValueError('%s KD not found, please use kd or kd.full' % args.kd)

n_param_student = count_parameters(student_encoder) + count_parameters(student_classifier)+ count_parameters(student_classifier_2)
logger.info('number of layers in student model = %d' % n_student_layer)
logger.info('num parameters in student model are %d and %d and %d and %d' % (count_parameters(student_encoder), count_parameters(student_classifier), count_parameters(student_classifier_2), count_parameters(student_classifier_3)))


#########################################################################
# Prepare optimizer
#########################################################################

if task_name == 'rte':
    log_per_step = 1    
elif task_name == 'mrpc':
    log_per_step = 1
elif task_name == 'cola':
    log_per_step = 5
elif task_name == 'sst-2':
    log_per_step = 40
else:
    log_per_step = 80 



if args.do_train:
    
    
##############################################################################################################################################    
    print('*'*77)    
        # Determine the layers to freeze
    if args.student_hidden_layers == 3:
        args.freeze_layer = [1,3,5,7,8,9]    
    elif args.student_hidden_layers == 6:
        args.freeze_layer = [1,3,5,7,9,11,13,14,15,16,17,18]    
    if args.freeze_layer is not None:    
        list_of_frozen_params = []
        list_of_frozen_params_L1 = []
        
        for name, param in student_encoder.named_parameters():
            if 'embeddings' in name:
                param.requires_grad = False
                list_of_frozen_params.append(name)
                list_of_frozen_params_L1.append(torch.mean(torch.abs(param)))
                
        for count in range(len(args.freeze_layer)):
            for name, param in student_encoder.named_parameters():
                if 'bert.encoder.layer.'+str(int(args.freeze_layer[count])-1)+'.' in name:
                    param.requires_grad = False
                    list_of_frozen_params.append(name)
                    list_of_frozen_params_L1.append(torch.mean(torch.abs(param)))
        
        for name, param in student_encoder.named_parameters():
            if 'pooler' in name:
                param.requires_grad = False
                list_of_frozen_params.append(name)
                list_of_frozen_params_L1.append(torch.mean(torch.abs(param)))
        print("Following are the list of params that are frozen")
        for a in range(len(list_of_frozen_params)):
            print(list_of_frozen_params[a])
            
    else:
        print("No layers are frozen")
        print('*'*77)
################################################################################################################################################    
    
    param_optimizer = list(student_encoder.named_parameters()) + list(student_classifier.named_parameters()) + list(student_classifier_2.named_parameters()) + list(student_classifier_3.named_parameters())
    no_decay = ['bias', 'LayerNorm.bias', 'LayerNorm.weight']    
    
    optimizer_grouped_parameters = [
        {'params': [p for n, p in param_optimizer if not any(nd in n for nd in no_decay)], 'weight_decay': 0.01},
        {'params': [p for n, p in param_optimizer if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
    ]
   
    if args.fp16:
        logger.info('FP16 activate, use apex FusedAdam')
        try:
            from apex.optimizers import FP16_Optimizer
            from apex.optimizers import FusedAdam
        except ImportError:
            raise ImportError(
                "Please install apex from https://www.github.com/nvidia/apex to use distributed and fp16 training.")

        optimizer = FusedAdam(optimizer_grouped_parameters,
                              lr=args.learning_rate,
                              bias_correction=False,
                              max_grad_norm=1.0)
        if args.loss_scale == 0:
            optimizer = FP16_Optimizer(optimizer, dynamic_loss_scale=True)
        else:
            optimizer = FP16_Optimizer(optimizer, static_loss_scale=args.loss_scale)
    else:
        logger.info('FP16 is not activated, use BertAdam')
        optimizer = BertAdam(optimizer_grouped_parameters,
                             lr=args.learning_rate,
                             warmup=args.warmup_proportion,
                             t_total=num_train_optimization_steps)


#########################################################################
# Model Training
#########################################################################
if args.do_train:
            
    global_step = 0
    nb_tr_steps = 0
    tr_loss = 0
    student_encoder.train()
    student_classifier.train()
    student_classifier_2.train()
    student_classifier_3.train()

    log_train = open(os.path.join(args.output_dir, 'train_log.txt'), 'w', buffering=1)
    log_eval = open(os.path.join(args.output_dir, 'eval_log.txt'), 'w', buffering=1)
    print('epoch,global_steps,step,acc,loss,kd_loss,ce_loss,AT_loss', file=log_train)
    print('epoch,acc,loss', file=log_eval)
    
             
    eval_best_acc_list = [0,0,0]
    eval_loss_min_list = [100,100,100]
    eval_best_acc_all = 0
    eval_best_acc_and_f1_all = 0
    eval_loss_min_all = 100
       
    
    for epoch in trange(int(args.num_train_epochs), desc="Epoch"):
        tr_loss, tr_ce_loss, tr_kd_loss, tr_acc = 0, 0, 0, 0
        nb_tr_examples, nb_tr_steps = 0, 0
        for step, batch in enumerate(tqdm(train_dataloader, desc="Iteration")):
            student_encoder.train()
            student_classifier.train()
            student_classifier_2.train()
            student_classifier_3.train()
            batch = tuple(t.to(device) for t in batch)
            if args.alpha == 0:
                input_ids, input_mask, segment_ids, label_ids = batch
                teacher_pred, teacher_patience = None, None
            else:
                if args.kd_model == 'kd':
                    input_ids, input_mask, segment_ids, label_ids, teacher_pred = batch
                    teacher_patience = None
                else:
                    input_ids, input_mask, segment_ids, label_ids, teacher_pred, teacher_patience = batch
                    if args.fp16:
                        teacher_patience = teacher_patience.half()
                if args.fp16:
                    teacher_pred = teacher_pred.half()

            full_output, full_output_2, full_output_3, pooled_output, pooled_output_2, pooled_output_3 = student_encoder(input_ids, segment_ids, input_mask, NL_mode = args.NL_mode)
                
            if args.kd_model.lower() in['kd', 'kd.cls']:
                if args.NL_mode == 0:
                    logits_pred_student = student_classifier(pooled_output)
                    logits_pred_student_2 = student_classifier_2(pooled_output_2)
                    logits_pred_student_3 = student_classifier_3(pooled_output_3)
                elif args.NL_mode == 1:
                    logits_pred_student_2 = student_classifier_2(pooled_output)
                    logits_pred_student_3 = student_classifier_3(pooled_output_3)
                elif args.NL_mode == 2:
                    logits_pred_student = student_classifier(pooled_output)
                    logits_pred_student_3 = student_classifier_3(pooled_output_3)
                elif args.NL_mode == 3:
                    logits_pred_student = student_classifier(pooled_output)
                    logits_pred_student_2 = student_classifier_2(pooled_output_2)
                
            
                if args.kd_model.lower() == 'kd.cls':
                    if args.NL_mode == 0:
                        student_patience = torch.stack(full_output[:-1]).transpose(0,1)
                        student_patience_2 = torch.stack(full_output_2[:-1]).transpose(0,1)
                        student_patience_3 = torch.stack(full_output_3[:-1]).transpose(0,1)
                    elif args.NL_mode == 1:
                        student_patience_2 = torch.stack(full_output_2[:-1]).transpose(0,1)
                        student_patience_3 = torch.stack(full_output_3[:-1]).transpose(0,1)
                    elif args.NL_mode == 2:
                        student_patience = torch.stack(full_output[:-1]).transpose(0,1)
                        student_patience_3 = torch.stack(full_output_3[:-1]).transpose(0,1)
                    elif args.NL_mode == 3:
                        student_patience = torch.stack(full_output[:-1]).transpose(0,1)
                        student_patience_2 = torch.stack(full_output_2[:-1]).transpose(0,1)
                        
                else:
                    student_patience = None
            elif args.kd_model.lower() == 'kd.full':
                logits_pred_student = student_classifier(full_output, weights, layer_idx)
            else:
                raise ValueError(f'{args.kd_model} not implemented yet')
            
            if args.NL_mode == 0:
                loss_dl, kd_loss, ce_loss = distillation_loss(logits_pred_student, label_ids, teacher_pred, T=args.T, alpha=args.alpha)
                loss_dl_2, kd_loss_2, ce_loss_2 = distillation_loss(logits_pred_student_2, label_ids, teacher_pred, T=args.T, alpha=args.alpha)
                loss_dl_3, kd_loss_3, ce_loss_3 = distillation_loss(logits_pred_student_3, label_ids, teacher_pred, T=args.T, alpha=args.alpha)
            elif args.NL_mode == 1:
                loss_dl_2, kd_loss_2, ce_loss_2 = distillation_loss(logits_pred_student_2, label_ids, teacher_pred, T=args.T, alpha=args.alpha)
                loss_dl_3, kd_loss_3, ce_loss_3 = distillation_loss(logits_pred_student_3, label_ids, teacher_pred, T=args.T, alpha=args.alpha)
            elif args.NL_mode == 2:
                loss_dl, kd_loss, ce_loss = distillation_loss(logits_pred_student, label_ids, teacher_pred, T=args.T, alpha=args.alpha)
                loss_dl_3, kd_loss_3, ce_loss_3 = distillation_loss(logits_pred_student_3, label_ids, teacher_pred, T=args.T, alpha=args.alpha)
            elif args.NL_mode == 3:
                loss_dl, kd_loss, ce_loss = distillation_loss(logits_pred_student, label_ids, teacher_pred, T=args.T, alpha=args.alpha)
                loss_dl_2, kd_loss_2, ce_loss_2 = distillation_loss(logits_pred_student_2, label_ids, teacher_pred, T=args.T, alpha=args.alpha)
            
            if args.beta > 0:
                if args.NL_mode == 0:
                    pt_loss = args.beta * patience_loss(teacher_patience, student_patience, args.normalize_patience)
                    pt_loss_2 = args.beta * patience_loss(teacher_patience, student_patience_2, args.normalize_patience)
                    pt_loss_3 = args.beta * patience_loss(teacher_patience, student_patience_3, args.normalize_patience)
                    loss = loss_dl + pt_loss + loss_dl_2 + pt_loss_2 + loss_dl_3 + pt_loss_3
                elif args.NL_mode == 1:
                    pt_loss_2 = args.beta * patience_loss(teacher_patience, student_patience_2, args.normalize_patience)
                    pt_loss_3 = args.beta * patience_loss(teacher_patience, student_patience_3, args.normalize_patience)
                    loss = loss_dl_2 + pt_loss_2 + loss_dl_3 + pt_loss_3
                elif args.NL_mode == 2:
                    pt_loss = args.beta * patience_loss(teacher_patience, student_patience, args.normalize_patience)
                    pt_loss_3 = args.beta * patience_loss(teacher_patience, student_patience_3, args.normalize_patience)
                    loss = loss_dl + pt_loss + loss_dl_3 + pt_loss_3
                elif args.NL_mode == 3:
                    pt_loss = args.beta * patience_loss(teacher_patience, student_patience, args.normalize_patience)
                    pt_loss_2 = args.beta * patience_loss(teacher_patience, student_patience_2, args.normalize_patience)
                    loss = loss_dl + pt_loss + loss_dl_2 + pt_loss_2                    
                 
            else:
                pt_loss = torch.tensor(0.0)
                if args.NL_mode == 0:
                    loss = loss_dl + loss_dl_2 + loss_dl_3
                elif args.NL_mode == 1:
                    loss = loss_dl_2 + loss_dl_3
                elif args.NL_mode == 2:
                    loss = loss_dl + loss_dl_3
                elif args.NL_mode == 3:
                    loss = loss_dl + loss_dl_2                    

            if n_gpu > 1:
                loss = loss.mean()  # mean() to average on multi-gpu.

            if args.fp16:
                optimizer.backward(loss)
            else:
                loss.backward()

            if args.gradient_accumulation_steps > 1:
                loss = loss / args.gradient_accumulation_steps

            if (step + 1) % args.gradient_accumulation_steps == 0:
                if args.fp16:
                    lr_this_step = args.learning_rate * warmup_linear(global_step / num_train_optimization_steps,
                                                                      args.warmup_proportion)
                    for param_group in optimizer.param_groups:
                        param_group['lr'] = lr_this_step
                
                else:
                    lr_this_step = args.learning_rate * warmup_linear(global_step / num_train_optimization_steps, args.warmup_proportion)
                    
                    for param_group in optimizer.param_groups:
                        param_group['lr'] = lr_this_step
                        
                optimizer.step()
                optimizer.zero_grad()
                global_step += 1

            if global_step % 50 == 0:
                if args.freeze_layer is not None:
                    error = 0
                    L1_list = []
                    name_list = []
                    print()
                    print('*'*77)
                    print("Checking if the parameters are indeed frozen")
                    for name, param in student_encoder.named_parameters():
                        if name in list_of_frozen_params:
                            L1_list.append(torch.mean(torch.abs(param)))
                            name_list.append(name)
#                             print(name+": "+str(torch.mean(torch.abs(param))))
                    for i in range(len(list_of_frozen_params_L1)):
                        if L1_list[i] != list_of_frozen_params_L1[i]:
                            print("Following parameters are not frozen")
                            print(name_list[i])
                            error +=1
                    if error !=0:
                        print("error has occured")
                    else:
                        print("Parameters are well frozen")
                    print('*'*77)    
#################################################################################################################################################### 
        #Save a trained model and the associated configuration
            if (global_step % log_per_step == 0) & (epoch > 0):
                if 'race' in task_name:
                    result = eval_model_dataloader_nli(student_encoder, student_classifier, eval_dataloader, device, False)
                else:
                    test_res = eval_model_dataloader_nli_NL(args.task_name.lower(), eval_label_ids, student_encoder, student_classifier, student_classifier_2, student_classifier_3, eval_dataloader, args.kd_model, num_labels, device, args.weights, args.fc_layer_idx, output_mode, NL_mode = args.NL_mode)
                           
                # Printing validation results and saving checkpoints when the conditions below are met.
                if task_name == 'mrpc':
                    loss_DT_1 = 0
                    loss_DT_2 = 0 
                    loss_Negotiator = 0
                    acc_DT_1 = 0
                    acc_DT_2 = 0 
                    acc_Negotiator = 0
                    acc_and_f1_DT_1 = 0
                    acc_and_f1_DT_2 = 0
                    acc_and_f1_Negotiator = 0
                    
                    if args.NL_mode == 0:
                        loss_all = test_res['eval_loss_DT_1'] + test_res['eval_loss_DT_2'] + test_res['eval_loss_Negotiator']
                        acc_all = test_res['acc_DT_1'] + test_res['acc_DT_2'] + test_res['acc_Negotiator']
                        loss_DT_1 = test_res['eval_loss_DT_1']
                        loss_DT_2 = test_res['eval_loss_DT_2']
                        loss_Negotiator = test_res['eval_loss_Negotiator']
                        acc_DT_1 = test_res['acc_DT_1']
                        acc_DT_2 = test_res['acc_DT_2']
                        acc_Negotiator = test_res['acc_Negotiator']
                        acc_and_f1_all = test_res['acc_and_f1_DT_1'] + test_res['acc_and_f1_DT_2'] + test_res['acc_and_f1_Negotiator']
                    elif args.NL_mode == 1:    
                        loss_all = test_res['eval_loss_DT_2'] + test_res['eval_loss_Negotiator'] 
                        acc_all = test_res['acc_DT_2'] + test_res['acc_Negotiator'] 
                        loss_DT_2 = test_res['eval_loss_DT_2']
                        loss_Negotiator = test_res['eval_loss_Negotiator']
                        acc_DT_2 = test_res['acc_DT_2']
                        acc_Negotiator = test_res['acc_Negotiator']                          
                        acc_and_f1_all = test_res['acc_and_f1_DT_2'] + test_res['acc_and_f1_Negotiator']
                    elif args.NL_mode == 2:
                        loss_all = test_res['eval_loss_DT_1'] + test_res['eval_loss_DT_2']
                        acc_all = test_res['acc_DT_1'] + test_res['acc_DT_2']
                        loss_DT_1 = test_res['eval_loss_DT_1']
                        loss_DT_2 = test_res['eval_loss_DT_2']
                        acc_DT_1 = test_res['acc_DT_1']
                        acc_DT_2 = test_res['acc_DT_2']
                        acc_and_f1_all = test_res['acc_and_f1_DT_1'] + test_res['acc_and_f1_DT_2']
                    elif args.NL_mode == 3:
                        loss_all = test_res['eval_loss_DT_1'] + test_res['eval_loss_Negotiator']
                        acc_all = test_res['acc_DT_1'] + test_res['acc_Negotiator']
                        loss_DT_1 = test_res['eval_loss_DT_1']
                        loss_Negotiator = test_res['eval_loss_Negotiator']
                        acc_DT_1 = test_res['acc_DT_1']
                        acc_Negotiator = test_res['acc_Negotiator']
                        acc_and_f1_all = test_res['acc_and_f1_DT_1'] + test_res['acc_and_f1_Negotiator']                        
                                                
                    if acc_all > eval_best_acc_all:
                        logger.info("")
                        logger.info('='*77)
                        logger.info("Validation acc_all improved! "+str(eval_best_acc_all)+" -> "+str(acc_all))
                        logger.info("DT_1 acc: "+str(test_res['acc_DT_1']))
                        logger.info("DT_2 acc: "+str(test_res['acc_DT_2']))
                        logger.info("Negotiator acc: "+str(acc_Negotiator))                        
                        logger.info('='*77)
                        eval_best_acc_all = acc_all
                        if eval_best_acc_all > args.saving_criterion_acc:
                            if args.n_gpu > 1:
                                torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_all.pkl'))
                                torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_all.pkl'))
                            else:
                                torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_all.pkl'))
                                torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_all.pkl'))
                            logger.info("Saving the model...")
  
                    if acc_and_f1_all > eval_best_acc_and_f1_all:
                        logger.info("")
                        logger.info('='*77)
                        logger.info("Validation acc_and_f1_all improved! "+str(eval_best_acc_and_f1_all)+" -> "+str(acc_and_f1_all))
                        logger.info("DT_1 acc and f1: "+str(test_res['acc_and_f1_DT_1']))
                        logger.info("DT_2 acc and f1: "+str(test_res['acc_and_f1_DT_2']))
                        logger.info("Negotiator acc and f1: "+str(test_res['acc_and_f1_Negotiator']))                        
                        logger.info('='*77)
                        eval_best_acc_and_f1_all = acc_and_f1_all
                        if eval_best_acc_and_f1_all > args.saving_criterion_acc:
                            if args.n_gpu > 1:
                                torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_and_f1_all.pkl'))
                                torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_and_f1_all.pkl'))
                            else:
                                torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_and_f1_all.pkl'))
                                torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_and_f1_all.pkl'))
                            logger.info("Saving the model...")
                            
                    if loss_all < eval_loss_min_all:
                        logger.info("")
                        logger.info('='*77)
                        logger.info("Validation loss_all improved! "+str(eval_loss_min_all)+" -> "+str(loss_all))
                        logger.info("DT_1 loss: "+str(loss_DT_1))
                        logger.info("DT_2 loss: "+str(loss_DT_2))
                        logger.info("Negotiator loss: "+str(loss_Negotiator))                         
                        logger.info('='*77)
                        eval_loss_min_all = loss_all
                        if eval_loss_min_all < args.saving_criterion_loss:
                            if args.n_gpu > 1:
                                torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_all.pkl'))
                                torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_all.pkl'))
                            else:
                                torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_all.pkl'))
                                torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_all.pkl'))
                            logger.info("Saving the model...")                            
                    
                    if args.NL_mode == 0: 
                        if test_res['acc_DT_1'] > eval_best_acc_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_1 improved! "+str(eval_best_acc_list[0])+" -> "+str(test_res['acc_DT_1']))
                            logger.info('='*77)
                            eval_best_acc_list[0] = test_res['acc_DT_1']
                            if eval_best_acc_list[0] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_1'] < eval_loss_min_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_1 improved! "+str(eval_loss_min_list[0])+" -> "+str(test_res['eval_loss_DT_1']))     
                            logger.info('='*77)
                            eval_loss_min_list[0] = test_res['eval_loss_DT_1']
                            if eval_loss_min_list[0] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                logger.info("Saving the model...")
                        
                        if test_res['acc_DT_2'] > eval_best_acc_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_2 improved! "+str(eval_best_acc_list[1])+" -> "+str(test_res['acc_DT_2']))
                            logger.info('='*77)
                            eval_best_acc_list[1] = test_res['acc_DT_2']
                            if eval_best_acc_list[1] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_2'] < eval_loss_min_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_2 improved! "+str(eval_loss_min_list[1])+" -> "+str(test_res['eval_loss_DT_2']))
                            logger.info('='*77)
                            eval_loss_min_list[1] = test_res['eval_loss_DT_2']
                            if eval_loss_min_list[1] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                logger.info("Saving the model...")

                        if test_res['acc_Negotiator'] > eval_best_acc_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_Negotiator improved! "+str(eval_best_acc_list[2])+" -> "+str(test_res['acc_Negotiator']))
                            logger.info('='*77)
                            eval_best_acc_list[2] = test_res['acc_Negotiator']
                            if eval_best_acc_list[2] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_Negotiator'] < eval_loss_min_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_Negotiator improved! "+str(eval_loss_min_list[2])+" -> "+str(test_res['eval_loss_Negotiator']))
                            logger.info('='*77)
                            eval_loss_min_list[2] = test_res['eval_loss_Negotiator']
                            if eval_loss_min_list[2] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                logger.info("Saving the model...")
                            
                    if args.NL_mode == 1: 
                        if test_res['acc_DT_2'] > eval_best_acc_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_2 improved! "+str(eval_best_acc_list[1])+" -> "+str(test_res['acc_DT_2']))
                            logger.info('='*77)
                            eval_best_acc_list[1] = test_res['acc_DT_2']
                            if eval_best_acc_list[1] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_2'] < eval_loss_min_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_2 improved! "+str(eval_loss_min_list[1])+" -> "+str(test_res['eval_loss_DT_2']))
                            logger.info('='*77)
                            eval_loss_min_list[1] = test_res['eval_loss_DT_2']
                            if eval_loss_min[1] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                logger.info("Saving the model...")

                        if test_res['acc_Negotiator'] > eval_best_acc_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_Negotiator improved! "+str(eval_best_acc_list[2])+" -> "+str(test_res['acc_Negotiator']))
                            logger.info('='*77)
                            eval_best_acc_list[2] = test_res['acc_Negotiator']
                            if eval_best_acc_list[2] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_Negotiator'] < eval_loss_min_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_Negotiator improved! "+str(eval_loss_min_list[2])+" -> "+str(test_res['eval_loss_Negotiator']))
                            logger.info('='*77)
                            eval_loss_min_list[2] = test_res['eval_loss_Negotiator']
                            if eval_loss_min_list[2] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                logger.info("Saving the model...")
                                
                    if args.NL_mode == 2: 
                        if test_res['acc_DT_1'] > eval_best_acc_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_1 improved! "+str(eval_best_acc_list[0])+" -> "+str(test_res['acc_DT_1']))
                            logger.info('='*77)
                            eval_best_acc_list[0] = test_res['acc_DT_1']
                            if eval_best_acc_list[0] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_1'] < eval_loss_min_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_1 improved! "+str(eval_loss_min_list[0])+" -> "+str(test_res['eval_loss_DT_1']))     
                            logger.info('='*77)
                            eval_loss_min_list[0] = test_res['eval_loss_DT_1']
                            if eval_loss_min_list[0] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                logger.info("Saving the model...")                        
                        
                        if test_res['acc_DT_2'] > eval_best_acc_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_2 improved! "+str(eval_best_acc_list[1])+" -> "+str(test_res['acc_DT_2']))
                            logger.info('='*77)
                            eval_best_acc_list[1] = test_res['acc_DT_2']
                            if eval_best_acc_list[1] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_2'] < eval_loss_min_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_2 improved! "+str(eval_loss_min_list[1])+" -> "+str(test_res['eval_loss_DT_2']))
                            logger.info('='*77)
                            eval_loss_min_list[1] = test_res['eval_loss_DT_2']
                            if eval_loss_min[1] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                logger.info("Saving the model...")

                    if args.NL_mode == 3: 
                        if test_res['acc_DT_1'] > eval_best_acc_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_1 improved! "+str(eval_best_acc_list[0])+" -> "+str(test_res['acc_DT_1']))
                            logger.info('='*77)
                            eval_best_acc_list[0] = test_res['acc_DT_1']
                            if eval_best_acc_list[0] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_1'] < eval_loss_min_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_1 improved! "+str(eval_loss_min_list[0])+" -> "+str(test_res['eval_loss_DT_1']))     
                            logger.info('='*77)
                            eval_loss_min_list[0] = test_res['eval_loss_DT_1']
                            if eval_loss_min_list[0] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                logger.info("Saving the model...")                        

                        if test_res['acc_Negotiator'] > eval_best_acc_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_Negotiator improved! "+str(eval_best_acc_list[2])+" -> "+str(test_res['acc_Negotiator']))
                            logger.info('='*77)
                            eval_best_acc_list[2] = test_res['acc_Negotiator']
                            if eval_best_acc_list[2] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_Negotiator'] < eval_loss_min_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_Negotiator improved! "+str(eval_loss_min_list[2])+" -> "+str(test_res['eval_loss_Negotiator']))
                            logger.info('='*77)
                            eval_loss_min_list[2] = test_res['eval_loss_Negotiator']
                            if eval_loss_min_list[2] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                logger.info("Saving the model...")
                                
#######################################################################################################################################        
                else:
                    loss_DT_1 = 0
                    loss_DT_2 = 0 
                    loss_Negotiator = 0
                    acc_DT_1 = 0
                    acc_DT_2 = 0 
                    acc_Negotiator = 0
                    if args.NL_mode == 0:
                        loss_all = test_res['eval_loss_DT_1'] + test_res['eval_loss_DT_2'] + test_res['eval_loss_Negotiator']
                        acc_all = test_res['acc_DT_1'] + test_res['acc_DT_2'] + test_res['acc_Negotiator']
                        loss_DT_1 = test_res['eval_loss_DT_1']
                        loss_DT_2 = test_res['eval_loss_DT_2']
                        loss_Negotiator = test_res['eval_loss_Negotiator']
                        acc_DT_1 = test_res['acc_DT_1']
                        acc_DT_2 = test_res['acc_DT_2']
                        acc_Negotiator = test_res['acc_Negotiator']
                    elif args.NL_mode == 1:    
                        loss_all = test_res['eval_loss_DT_2'] + test_res['eval_loss_Negotiator'] 
                        acc_all = test_res['acc_DT_2'] + test_res['acc_Negotiator'] 
                        loss_DT_2 = test_res['eval_loss_DT_2']
                        loss_Negotiator = test_res['eval_loss_Negotiator']
                        acc_DT_2 = test_res['acc_DT_2']
                        acc_Negotiator = test_res['acc_Negotiator']
                    elif args.NL_mode == 2:
                        loss_all = test_res['eval_loss_DT_1'] + test_res['eval_loss_DT_2']
                        acc_all = test_res['acc_DT_1'] + test_res['acc_DT_2']
                        loss_DT_1 = test_res['eval_loss_DT_1']
                        loss_DT_2 = test_res['eval_loss_DT_2']
                        acc_DT_1 = test_res['acc_DT_1']
                        acc_DT_2 = test_res['acc_DT_2']
                    elif args.NL_mode == 3:
                        loss_all = test_res['eval_loss_DT_1'] + test_res['eval_loss_Negotiator']
                        acc_all = test_res['acc_DT_1'] + test_res['acc_Negotiator']
                        loss_DT_1 = test_res['eval_loss_DT_1']
                        loss_Negotiator = test_res['eval_loss_Negotiator']
                        acc_DT_1 = test_res['acc_DT_1']
                        acc_Negotiator = test_res['acc_Negotiator']                       
                                                    
                    if acc_all > eval_best_acc_all:
                        logger.info("")
                        logger.info('='*77)
                        logger.info("Validation acc_all improved! "+str(eval_best_acc_all)+" -> "+str(acc_all))
                        logger.info("DT_1 acc: "+str(acc_DT_1))
                        logger.info("DT_2 acc: "+str(acc_DT_2))
                        logger.info("Negotiator acc: "+str(acc_Negotiator))                        
                        logger.info('='*77)
                        eval_best_acc_all = acc_all
                        if eval_best_acc_all > args.saving_criterion_acc:
                            if args.n_gpu > 1:
                                torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_all.pkl'))
                                torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_all.pkl'))
                            else:
                                torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_all.pkl'))
                                torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_all.pkl'))
                            logger.info("Saving the model...")
                            
                    if loss_all < eval_loss_min_all:
                        logger.info("")
                        logger.info('='*77)
                        logger.info("Validation loss_all improved! "+str(eval_loss_min_all)+" -> "+str(loss_all))
                        logger.info("DT_1 loss: "+str(loss_DT_1))
                        logger.info("DT_2 loss: "+str(loss_DT_2))
                        logger.info("Negotiator loss: "+str(loss_Negotiator))                         
                        logger.info('='*77)
                        eval_loss_min_all = loss_all
                        if eval_loss_min_all < args.saving_criterion_loss:
                            if args.n_gpu > 1:
                                torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_all.pkl'))
                                torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_all.pkl'))
                            else:
                                torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_all.pkl'))
                                torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_all.pkl'))
                            logger.info("Saving the model...")                            
                    
                    if args.NL_mode == 0: 
                        if test_res['acc_DT_1'] > eval_best_acc_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_1 improved! "+str(eval_best_acc_list[0])+" -> "+str(test_res['acc_DT_1']))
                            logger.info('='*77)
                            eval_best_acc_list[0] = test_res['acc_DT_1']
                            if eval_best_acc_list[0] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_1'] < eval_loss_min_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_1 improved! "+str(eval_loss_min_list[0])+" -> "+str(test_res['eval_loss_DT_1']))     
                            logger.info('='*77)
                            eval_loss_min_list[0] = test_res['eval_loss_DT_1']
                            if eval_loss_min_list[0] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                logger.info("Saving the model...")
                                                
                        if test_res['acc_DT_2'] > eval_best_acc_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_2 improved! "+str(eval_best_acc_list[1])+" -> "+str(test_res['acc_DT_2']))
                            logger.info('='*77)
                            eval_best_acc_list[1] = test_res['acc_DT_2']
                            if eval_best_acc_list[1] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_2'] < eval_loss_min_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_2 improved! "+str(eval_loss_min_list[1])+" -> "+str(test_res['eval_loss_DT_2']))
                            logger.info('='*77)
                            eval_loss_min_list[1] = test_res['eval_loss_DT_2']
                            if eval_loss_min_list[1] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                logger.info("Saving the model...")

                        if test_res['acc_Negotiator'] > eval_best_acc_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_Negotiator improved! "+str(eval_best_acc_list[2])+" -> "+str(test_res['acc_Negotiator']))
                            logger.info('='*77)
                            eval_best_acc_list[2] = test_res['acc_Negotiator']
                            if eval_best_acc_list[2] > args.saving_criterion_acc:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_Negotiator'] < eval_loss_min_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_Negotiator improved! "+str(eval_loss_min_list[2])+" -> "+str(test_res['eval_loss_Negotiator']))
                            logger.info('='*77)
                            eval_loss_min_list[2] = test_res['eval_loss_Negotiator']
                            if eval_loss_min_list[2] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                logger.info("Saving the model...")
                            
                    if args.NL_mode == 1: 
                        if test_res['acc_Negotiator'] > eval_best_acc_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_Negotiator improved! "+str(eval_best_acc_list[2])+" -> "+str(test_res['acc_Negotiator']))
                            logger.info('='*77)
                            eval_best_acc_list[2] = test_res['acc_Negotiator']
                            if eval_best_acc_list[2] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_Negotiator'] < eval_loss_min_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_Negotiator improved! "+str(eval_loss_min_list[2])+" -> "+str(test_res['eval_loss_Negotiator']))
                            logger.info('='*77)
                            eval_loss_min_list[2] = test_res['eval_loss_Negotiator']
                            if eval_loss_min_list[2] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                logger.info("Saving the model...")
                        
                        if test_res['acc_DT_2'] > eval_best_acc_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_2 improved! "+str(eval_best_acc_list[1])+" -> "+str(test_res['acc_DT_2']))
                            logger.info('='*77)
                            eval_best_acc_list[1] = test_res['acc_DT_2']
                            if eval_best_acc_list[1] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_2'] < eval_loss_min_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_2 improved! "+str(eval_loss_min_list[1])+" -> "+str(test_res['eval_loss_DT_2']))
                            logger.info('='*77)
                            eval_loss_min_list[1] = test_res['eval_loss_DT_2']
                            if eval_loss_min[1] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                logger.info("Saving the model...")
                                
                    if args.NL_mode == 2: 
                        if test_res['acc_DT_1'] > eval_best_acc_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_1 improved! "+str(eval_best_acc_list[0])+" -> "+str(test_res['acc_DT_1']))
                            logger.info('='*77)
                            eval_best_acc_list[0] = test_res['acc_DT_1']
                            if eval_best_acc_list[0] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_1'] < eval_loss_min_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_1 improved! "+str(eval_loss_min_list[0])+" -> "+str(test_res['eval_loss_DT_1']))     
                            logger.info('='*77)
                            eval_loss_min_list[0] = test_res['eval_loss_DT_1']
                            if eval_loss_min_list[0] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                logger.info("Saving the model...")
                                                            
                        if test_res['acc_DT_2'] > eval_best_acc_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_2 improved! "+str(eval_best_acc_list[1])+" -> "+str(test_res['acc_DT_2']))
                            logger.info('='*77)
                            eval_best_acc_list[1] = test_res['acc_DT_2']
                            if eval_best_acc_list[1] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_2.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_2'] < eval_loss_min_list[1]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_2 improved! "+str(eval_loss_min_list[1])+" -> "+str(test_res['eval_loss_DT_2']))
                            logger.info('='*77)
                            eval_loss_min_list[1] = test_res['eval_loss_DT_2']
                            if eval_loss_min[1] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_2.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_2.pkl'))
                                logger.info("Saving the model...")
                            
                    if args.NL_mode == 3: 
                        if test_res['acc_DT_1'] > eval_best_acc_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_DT_1 improved! "+str(eval_best_acc_list[0])+" -> "+str(test_res['acc_DT_1']))
                            logger.info('='*77)
                            eval_best_acc_list[0] = test_res['acc_DT_1']
                            if eval_best_acc_list[0] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_DT_1.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_DT_1'] < eval_loss_min_list[0]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_DT_1 improved! "+str(eval_loss_min_list[0])+" -> "+str(test_res['eval_loss_DT_1']))     
                            logger.info('='*77)
                            eval_loss_min_list[0] = test_res['eval_loss_DT_1']
                            if eval_loss_min_list[0] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_DT_1.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_DT_1.pkl'))
                                logger.info("Saving the model...")
                                                                                        
                        if test_res['acc_Negotiator'] > eval_best_acc_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation acc_Negotiator improved! "+str(eval_best_acc_list[2])+" -> "+str(test_res['acc_Negotiator']))
                            logger.info('='*77)
                            eval_best_acc_list[2] = test_res['acc_Negotiator']
                            if eval_best_acc_list[2] > 1:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_acc_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_acc_Negotiator.pkl'))
                                logger.info("Saving the model...")
                                
                        if test_res['eval_loss_Negotiator'] < eval_loss_min_list[2]:
                            logger.info("")
                            logger.info('='*77)
                            logger.info("Validation loss_Negotiator improved! "+str(eval_loss_min_list[2])+" -> "+str(test_res['eval_loss_Negotiator']))
                            logger.info('='*77)
                            eval_loss_min_list[2] = test_res['eval_loss_Negotiator']
                            if eval_loss_min_list[2] < 0:
                                if args.n_gpu > 1:
                                    torch.save(student_encoder.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.module.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                else:
                                    torch.save(student_encoder.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.encoder_loss_Negotiator.pkl'))
                                    torch.save(student_classifier.state_dict(), os.path.join(args.output_dir, 'BERT'+f'.cls_loss_Negotiator.pkl'))
                                logger.info("Saving the model...")                                               
                                               
logger.info("")
logger.info('='*77)
logger.info("Best Loss_1 : "+ str(eval_loss_min_list[0])+ "Best Acc_1 : "+str(eval_best_acc_list[0]))
logger.info("Best Loss_2 : "+ str(eval_loss_min_list[1])+ "Best Acc_2 : "+str(eval_best_acc_list[1]))
logger.info("Best Loss_3 : "+ str(eval_loss_min_list[2])+ "Best Acc_3 : "+str(eval_best_acc_list[2]))
logger.info("Best Loss_all : "+ str(eval_loss_min_all)+ "Best Acc all : "+str(eval_best_acc_all))
logger.info('='*77)
