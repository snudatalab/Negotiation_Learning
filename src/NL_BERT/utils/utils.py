"""
File used to define useful utility functions during training. Mainly based on [GitHub repository](https://github.com/intersun/PKD-for-BERT-Model-Compression) for [Patient Knowledge Distillation for BERT Model Compression](https://arxiv.org/abs/1908.09355).
"""
import logging
import torch
import os

import numpy as np
from torch.nn import CrossEntropyLoss, MSELoss
from torch import nn
from tqdm import tqdm

from utils.nli_data_processing import compute_metrics


logger = logging.getLogger(__name__)


def fill_tensor(tensor, batch_size):
    """
    for DataDistributed problem in pytorch  ...
    :param tensor:
    :param batch_size:
    :return:
    """
    if len(tensor) % batch_size != 0:
        diff = batch_size - len(tensor) % batch_size
        tensor += tensor[:diff]
    return tensor


def count_parameters(model, trainable_only=True, is_dict=False):
    if is_dict:
        return sum(np.prod(list(model[k].size())) for k in model)
    if trainable_only:
        return sum(p.numel() for p in model.parameters() if p.requires_grad)
    else:
        return sum(p.numel() for p in model.parameters())

def load_model(model, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """

    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
        model_state_dict = torch.load(checkpoint)
        old_keys = []
        new_keys = []
        pretrained_dict = dict()
        
        for key, values in model_state_dict.items():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
           
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
        pretrained_dict = {k: v for k, v in model_state_dict.items()}
        count = 0
        
        for key, values in model_state_dict.items():
            for count in range(args.student_hidden_layers):
                if key == "bert.encoder.layer."+str(count)+".attention.self.value.weight":
                    new_key = "bert.encoder.layer."+str(count)+".attention.self.v_2.weight"
                    pretrained_dict.update({new_key: model_state_dict[key]})
            
                if key == "bert.encoder.layer."+str(count)+".attention.self.value.bias":
                    new_key = "bert.encoder.layer."+str(count)+".attention.self.v_2.bias"
                    pretrained_dict.update({new_key: model_state_dict[key]})
            
#                 if key == "bert.encoder.layer."+str(count)+".output.dense.weight":
#                     new_key = "bert.encoder.layer."+str(count)+".output_2.dense.weight"
#                     pretrained_dict.update({new_key: model_state_dict[key]})
            
#                 if key == "bert.encoder.layer."+str(count)+".output.dense.bias":
#                     new_key = "bert.encoder.layer."+str(count)+".output_2.dense.bias"
#                     pretrained_dict.update({new_key: model_state_dict[key]})
            
#                 if key == "bert.encoder.layer."+str(count)+".output.LayerNorm.weight":
#                     new_key = "bert.encoder.layer."+str(count)+".output_2.LayerNorm.weight"
#                     pretrained_dict.update({new_key: model_state_dict[key]})
            
#                 if key == "bert.encoder.layer."+str(count)+".output.LayerNorm.bias":
#                     new_key = "bert.encoder.layer."+str(count)+".output_2.LayerNorm.bias"
#                     pretrained_dict.update({new_key: model_state_dict[key]})
                
            #if key == "bert.encoder.layer."+str(count)+".attention.self.value.weight":
            #    neww_key = "bert.encoder.layer."+str(count)+".attention.self.v_2.weight"
            #    neww_values = values
            #    model_state_dict.update({'neww_key' : neww_values})
        
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(pretrained_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del pretrained_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(pretrained_dict.keys()):
                if 'classifier' not in t:
                    del pretrained_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(pretrained_dict.keys()):
                if t not in model_keys:
                    del pretrained_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
        #print("Checking")
        #for key, values in pretrained_dict.items():
        #    print(key)
        model.load_state_dict(pretrained_dict)
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

def load_model_2(model, checkpoint_1, checkpoint_2, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """

    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint_1 in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint_1):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from ckp1: %s and  ckp2 : %s' % (model._get_name(), checkpoint_1, checkpoint_2))
        model_state_dict_1 = torch.load(checkpoint_1)
        model_state_dict_2 = torch.load(checkpoint_2)
        old_keys_1 = []
        new_keys_1 = []
        old_keys_2 = []
        new_keys_2 = []
        pretrained_dict = dict()
        for key, values in model_state_dict_1.items():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_key_1s.append(key)
                new_keys_1.append(new_key)
           
        for old_key, new_key in zip(old_keys_1, new_keys_1):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
        pretrained_dict = {k: v for k, v in model_state_dict_1.items()}
        
        
        key = "bert.pooler.dense.weight"
        pretrained_dict.update({key: model_state_dict_2[key]})
        key = "bert.pooler.dense.bias"
        pretrained_dict.update({key: model_state_dict_2[key]})
        #for key, values in model_state_dict_2.items():
        for count in range(3):
            key = "bert.encoder.layer."+str(count)+".attention.self.query.weight"
            new_key = "bert.encoder.layer."+str(count+3)+".attention.self.query.weight"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".attention.self.query.bias"
            new_key = "bert.encoder.layer."+str(count+3)+".attention.self.query.bias"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".attention.self.key.weight"
            new_key = "bert.encoder.layer."+str(count+3)+".attention.self.key.weight"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".attention.self.key.bias"
            new_key = "bert.encoder.layer."+str(count+3)+".attention.self.key.bias"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".attention.self.value.weight"
            new_key = "bert.encoder.layer."+str(count+3)+".attention.self.value.weight"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".attention.self.value.bias"
            new_key = "bert.encoder.layer."+str(count+3)+".attention.self.value.bias"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            print("kk")
            key = "bert.encoder.layer."+str(count)+".attention.output.dense.weight"
            new_key = "bert.encoder.layer."+str(count+3)+".attention.output.dense.weight"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".attention.output.dense.bias"
            new_key = "bert.encoder.layer."+str(count+3)+".attention.output.dense.bias"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".attention.output.LayerNorm.weight"
            new_key = "bert.encoder.layer."+str(count+3)+".attention.output.LayerNorm.weight"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".attention.output.LayerNorm.bias"
            new_key = "bert.encoder.layer."+str(count+3)+".attention.output.LayerNorm.bias"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".intermediate.dense.weight"
            new_key = "bert.encoder.layer."+str(count+3)+".intermediate.dense.weight"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".intermediate.dense.bias"
            new_key = "bert.encoder.layer."+str(count+3)+".intermediate.dense.bias"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".output.dense.weight"
            new_key = "bert.encoder.layer."+str(count+3)+".output.dense.weight"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".output.dense.bias"
            new_key = "bert.encoder.layer."+str(count+3)+".output.dense.bias"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".output.LayerNorm.weight"
            new_key = "bert.encoder.layer."+str(count+3)+".output.LayerNorm.weight"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            key = "bert.encoder.layer."+str(count)+".output.LayerNorm.bias"
            new_key = "bert.encoder.layer."+str(count+3)+".output.LayerNorm.bias"
            pretrained_dict.update({new_key: model_state_dict_2[key]})
            
            
            
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(pretrained_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del pretrained_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(pretrained_dict.keys()):
                if 'classifier' not in t:
                    del pretrained_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(pretrained_dict.keys()):
                if t not in model_keys:
                    del pretrained_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
            
            
        model.load_state_dict(pretrained_dict)
        
        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

# def load_model_wonbon(model, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
#     """

#     :param model:
#     :param checkpoint:
#     :param argstrain:
#     :param mode:  this is created because for old training the encoder and classifier are mixed together
#                   also adding student mode
#     :param train_mode:
#     :param verbose:
#     :return:
#     """

#     n_gpu = args.n_gpu
#     device = args.device
#     local_rank = -1
#     if checkpoint in [None, 'None']:
#         if verbose:
#             logger.info('no checkpoint provided for %s!' % model._get_name())
#     else:
#         if not os.path.exists(checkpoint):
#             raise ValueError('checkpoint %s not exist' % checkpoint)
#         if verbose:
#             logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
#         model_state_dict = torch.load(checkpoint)
#         old_keys = []
#         new_keys = []
#         for key in model_state_dict.keys():
#             new_key = None
#             if 'gamma' in key:
#                 new_key = key.replace('gamma', 'weight')
#             if 'beta' in key:
#                 new_key = key.replace('beta', 'bias')
#             if key.startswith('module.'):
#                 new_key = key.replace('module.', '')
#             if new_key:
#                 old_keys.append(key)
#                 new_keys.append(new_key)
#         for old_key, new_key in zip(old_keys, new_keys):
#             model_state_dict[new_key] = model_state_dict.pop(old_key)
        
#         for count in range(args.student_hidden_layers):
#             key = "bert.encoder.layer."+str(count)+".attention.self.query.weight"
#             new_key = "bert.encoder.layer."+str(count)+".attention.self.query.quantized_weight"
#             model_state_dict.update({new_key: model_state_dict[key]})
            
#             key = "bert.encoder.layer."+str(count)+".attention.self.key.weight"
#             new_key = "bert.encoder.layer."+str(count)+".attention.self.key.quantized_weight"
#             model_state_dict.update({new_key: model_state_dict[key]})
            
#             key = "bert.encoder.layer."+str(count)+".attention.self.value.weight"
#             new_key = "bert.encoder.layer."+str(count)+".attention.self.value.quantized_weight"
#             model_state_dict.update({new_key: model_state_dict[key]})
        
#         del_keys = []
#         keep_keys = []
#         if mode == 'exact':
#             pass
#         elif mode == 'encoder':
#             for t in list(model_state_dict.keys()):
#                 if 'classifier' in t or 'cls' in t:
#                     del model_state_dict[t]
#                     del_keys.append(t)
#                 else:
#                     keep_keys.append(t)
#         elif mode == 'classifier':
#             for t in list(model_state_dict.keys()):
#                 if 'classifier' not in t:
#                     del model_state_dict[t]
#                     del_keys.append(t)
#                 else:
#                     keep_keys.append(t)
#         elif mode == 'student':
#             model_keys = model.state_dict().keys()
#             for t in list(model_state_dict.keys()):
#                 if t not in model_keys:
#                     del model_state_dict[t]
#                     del_keys.append(t)
#                 else:
#                     keep_keys.append(t)
#         else:
#             raise ValueError('%s not available for now' % mode)
            
            
            
            
#         model.load_state_dict(model_state_dict)
        
        
        
        
        
#         if mode != 'exact':
#             logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
#         if DEBUG:
#             print('deleted keys =\n {}'.format('\n'.join(del_keys)))
#             print('*' * 77)
#             print('kept keys =\n {}'.format('\n'.join(keep_keys)))

#     if args.fp16:
#         logger.info('fp16 activated, now call model.half()')
#         model.half()
#     model.to(device)

#     if train_mode != 'finetune':
#         if verbose:
#             logger.info('freeze BERT layer in DEBUG mode')
#         model.set_mode(train_mode)

#     if local_rank != -1:
#         raise NotImplementedError('not implemented for local_rank != 1')
#     elif n_gpu > 1:
#         logger.info('data parallel because more than one gpu')
#         model = torch.nn.DataParallel(model)
#     return model

def load_model_real_wonbon(model, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """

    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
        model_state_dict = torch.load(checkpoint)
        old_keys = []
        new_keys = []
        for key in model_state_dict.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
        
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(model_state_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(model_state_dict.keys()):
                if 'classifier' not in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(model_state_dict.keys()):
                if t not in model_keys:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
            
            
            
        model.load_state_dict(model_state_dict)
        
        
        
        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

def load_model_real_wonbon_2(model, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """

    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
        model_state_dict = torch.load(checkpoint)
        old_keys = []
        new_keys = []
        for key in model_state_dict.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
        
        for count in range(args.student_hidden_layers):
            for key in model_state_dict.keys():
                new_key = None
                if 'bert.encoder.layer.'+str(count) in key:
                    new_key = key.replace(str(count), str(2*(count)+1))
                    print(key)
                    print("is changed to")
                    print(new_key)
                    model_state_dict.update({key: model_state_dict[new_key]})        
        
        
        
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(model_state_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(model_state_dict.keys()):
                if 'classifier' not in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(model_state_dict.keys()):
                if t not in model_keys:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
            
            
            
        model.load_state_dict(model_state_dict)
        
        
        
        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

def load_model_real_wonbon_456(model, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """

    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
        model_state_dict = torch.load(checkpoint)
        old_keys = []
        new_keys = []
        for key in model_state_dict.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
        
        for count in range(args.student_hidden_layers):
            for key in model_state_dict.keys():
                new_key = None
                if 'bert.encoder.layer.'+str(count) in key:
                    new_key = key.replace(str(count), str(count+3))
                    print(key)
                    print("is changed to")
                    print(new_key)
                    model_state_dict.update({key: model_state_dict[new_key]})        
        
                
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(model_state_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(model_state_dict.keys()):
                if 'classifier' not in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(model_state_dict.keys()):
                if t not in model_keys:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
            
            
            
        model.load_state_dict(model_state_dict)
        
        
        
        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model


def load_model_real_wonbon_135(model, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """

    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
        model_state_dict = torch.load(checkpoint)
        old_keys = []
        new_keys = []
        for key in model_state_dict.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
        
        for count in range(args.student_hidden_layers):
            for key in model_state_dict.keys():
                new_key = None
                if 'bert.encoder.layer.'+str(count) in key:
                    new_key = key.replace(str(count), str(2*count))
                    print(key)
                    print("is changed to")
                    print(new_key)
                    model_state_dict.update({key: model_state_dict[new_key]})        
        
        
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(model_state_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(model_state_dict.keys()):
                if 'classifier' not in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(model_state_dict.keys()):
                if t not in model_keys:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
            
            
            
        model.load_state_dict(model_state_dict)
        
        
        
        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

def load_model_real_wonbon_246(model, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """

    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
        model_state_dict = torch.load(checkpoint)
        old_keys = []
        new_keys = []
        for key in model_state_dict.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
        
        model_state_dict_0 = model_state_dict.copy()
        torch.set_printoptions(precision=10)
        for count in range(12):
            L1_norm = 0
            for key in model_state_dict.keys():
                if 'bert.encoder.layer.'+str(count) in key:
                    L1_norm += torch.mean(torch.abs(model_state_dict[key]))
            print("Layer-"+str(count)+" L1 norm : "+str(L1_norm))        
        
        
        for count in range(args.student_hidden_layers):
            for key in model_state_dict_0.keys():
                if count == 0:
                    new_key = None
                    if 'bert.encoder.layer.'+str(count) in key:
                        new_key = key.replace(str(count), str(1))
                        print(key)
                        print("is changed to")
                        print(new_key)
                        model_state_dict.update({key: model_state_dict_0[new_key]})  
                if count == 1:
                    new_key = None
                    if 'bert.encoder.layer.'+str(count) in key:
                        new_key = key.replace(str(count), str(3))
                        print(key)
                        print("is changed to")
                        print(new_key)
                        model_state_dict.update({key: model_state_dict_0[new_key]})  
                if count == 2:
                    new_key = None
                    if 'bert.encoder.layer.'+str(count) in key:
                        new_key = key.replace(str(count), str(4))
                        print(key)
                        print("is changed to")
                        print(new_key)
                        model_state_dict.update({key: model_state_dict_0[new_key]})                          
        
        for count in range(args.student_hidden_layers):
            L1_norm_original = 0
            L1_norm_final = 0
            for key in model_state_dict.keys():
                if 'bert.encoder.layer.'+str(count) in key:
                    L1_norm_final += torch.mean(torch.abs(model_state_dict[key]))
            print("Final Layer-"+str(count)+" L1 norm : "+str(L1_norm_final))
                
        
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(model_state_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(model_state_dict.keys()):
                if 'classifier' not in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(model_state_dict.keys()):
                if t not in model_keys:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
            
            
            
        model.load_state_dict(model_state_dict)
        
        
        
        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

def load_model_finetune(model, layer_initialization, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """
    
    
    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
        model_state_dict = torch.load(checkpoint)
        old_keys = []
        new_keys = []
        for key in model_state_dict.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
        
            
        model_state_dict_0 = model_state_dict.copy()
        torch.set_printoptions(precision=10)
                
        for count in range(args.student_hidden_layers):
            target_layer = int(layer_initialization[count])-1
            for key in model_state_dict_0.keys():
                new_key = None
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(target_layer))
                    model_state_dict.update({key: model_state_dict_0[new_key]})
                
        error = 0
        for count in range(args.student_hidden_layers):
            target_layer_num = int(layer_initialization[count])-1
            for key in model_state_dict.keys():
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(target_layer_num))
                    if torch.mean(torch.abs(model_state_dict[key])) != torch.mean(torch.abs(model_state_dict_0[new_key])):
                        error+=1
                    
        if error != 0:
            print("Error has occured")
        elif error == 0:
            for count in range(args.student_hidden_layers):
                print("Layer "+str(count+1)+" = Original checkpoint's "+str(layer_initialization[count])+"-th Layer")
                
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(model_state_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(model_state_dict.keys()):
                if 'classifier' not in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(model_state_dict.keys()):
                if t not in model_keys:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
            
            
            
        model.load_state_dict(model_state_dict)
        
        
        
        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

def load_model_from_distilbert(model, layer_initialization, checkpoint_distilbert, checkpoint_bert_base, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """
    
    
    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint_distilbert in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint_distilbert):
            raise ValueError('checkpoint %s not exist' % checkpoint_distilbert)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint_distilbert))

        model_state_dict_distilbert = torch.load(checkpoint_distilbert)
        old_keys = []
        new_keys = []
        for key in model_state_dict_distilbert.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict_distilbert[new_key] = model_state_dict_distilbert.pop(old_key)
            
        model_state_dict_bert_base = torch.load(checkpoint_bert_base)
        old_keys = []
        new_keys = []
        for key in model_state_dict_bert_base.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict_bert_base[new_key] = model_state_dict_bert_base.pop(old_key)        
            
        model_state_dict_distilbert_0 = model_state_dict_distilbert.copy()
        model_state_dict_bert_base_0 = model_state_dict_bert_base.copy()
        torch.set_printoptions(precision=10)
                
        # first change the names of parameters from distilbert to original bert.
        for key in model_state_dict_distilbert.keys():
            new_key = None
            if 'distilbert' in key:
                new_key = key.replace('distilbert', 'bert')
                model_state_dict_distilbert_0.update({new_key: model_state_dict_distilbert[key]})
                del model_state_dict_distilbert_0[key]
        
        model_state_dict_distilbert_temp = model_state_dict_distilbert_0.copy()

        for key in model_state_dict_distilbert_temp.keys():
            new_key = None
            if 'transformer' in key:
                new_key = key.replace('transformer', 'encoder')
                model_state_dict_distilbert_0.update({new_key: model_state_dict_distilbert_temp[key]})
                del model_state_dict_distilbert_0[key]
        
        model_state_dict_distilbert_temp = model_state_dict_distilbert_0.copy()
        
        for key in model_state_dict_distilbert_temp.keys():
            new_key = None
            if 'q_lin' in key:
                new_key = key.replace('q_lin', 'self.query')
                model_state_dict_distilbert_0.update({new_key: model_state_dict_distilbert_temp[key]})
                del model_state_dict_distilbert_0[key]
            elif 'k_lin' in key:
                new_key = key.replace('k_lin', 'self.key')
                model_state_dict_distilbert_0.update({new_key: model_state_dict_distilbert_temp[key]})
                del model_state_dict_distilbert_0[key]
            elif 'v_lin' in key:
                new_key = key.replace('v_lin', 'self.value')
                model_state_dict_distilbert_0.update({new_key: model_state_dict_distilbert_temp[key]})
                del model_state_dict_distilbert_0[key]
            elif 'attention.out_lin.' in key:
                new_key = key.replace('attention.out_lin', 'attention.output.dense')
                model_state_dict_distilbert_0.update({new_key: model_state_dict_distilbert_temp[key]})
                del model_state_dict_distilbert_0[key]
            elif 'sa_layer_norm' in key:
                new_key = key.replace('sa_layer_norm', 'attention.output.LayerNorm')
                model_state_dict_distilbert_0.update({new_key: model_state_dict_distilbert_temp[key]})
                del model_state_dict_distilbert_0[key]
            elif 'ffn.lin1' in key:
                new_key = key.replace('ffn.lin1.', 'intermediate.dense.')
                model_state_dict_distilbert_0.update({new_key: model_state_dict_distilbert_temp[key]})
                del model_state_dict_distilbert_0[key]
            elif 'ffn.lin2' in key:
                new_key = key.replace('ffn.lin2.', 'output.dense.')
                model_state_dict_distilbert_0.update({new_key: model_state_dict_distilbert_temp[key]})
                del model_state_dict_distilbert_0[key]
            elif 'output_layer_norm' in key:
                new_key = key.replace('output_layer_norm', 'output.LayerNorm')
                model_state_dict_distilbert_0.update({new_key: model_state_dict_distilbert_temp[key]})
                del model_state_dict_distilbert_0[key]
            elif 'vocab_transform' in key:
                new_key = key.replace('vocab_transform', 'bert.pooler.dense')
                model_state_dict_distilbert_0.update({new_key: model_state_dict_distilbert_temp[key]})
                del model_state_dict_distilbert_0[key]        
        
        for key in model_state_dict_bert_base.keys():
            if 'token_type' in key:
                model_state_dict_distilbert_0.update({key: model_state_dict_bert_base[key]})
                
        for count in range(args.student_hidden_layers):
            target_layer = int(layer_initialization[count])-1
            for key in model_state_dict_distilbert_0.keys():
                new_key = None
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(target_layer))
                    model_state_dict_distilbert_0.update({key: model_state_dict_distilbert_0[new_key]})
                
        error = 0
        for count in range(args.student_hidden_layers):
            target_layer_num = int(layer_initialization[count])-1
            for key in model_state_dict_distilbert.keys():
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(target_layer_num))
                    if torch.mean(torch.abs(model_state_dict_distilbert_0[key])) != torch.mean(torch.abs(model_state_dict_distilbert[new_key])):
                        error+=1
                    
        if error != 0:
            print("Error has occured")
        elif error == 0:
            for count in range(args.student_hidden_layers):
                print("Layer "+str(count+1)+" = Original checkpoint's "+str(layer_initialization[count])+"-th Layer")
                
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(model_state_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(model_state_dict.keys()):
                if 'classifier' not in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(model_state_dict_distilbert_0.keys()):
                if t not in model_keys:
                    del model_state_dict_distilbert_0[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
            
            
            
        model.load_state_dict(model_state_dict_distilbert_0)
        
        
        
        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))            
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

def load_model_from_ss(model, layer_initialization, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """
    
    
    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
        model_state_dict = torch.load(checkpoint)
        old_keys = []
        new_keys = []
        for key in model_state_dict.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
        
            
        model_state_dict_0 = model_state_dict.copy()
        torch.set_printoptions(precision=10)
        
        model_state_dict_ = model_state_dict.copy()
        for t in list(model_state_dict_.keys()):
            del model_state_dict_[t]
        
        for key in model_state_dict_.keys():
            print(key)
        
        print('*'*77)
        for key in model_state_dict_0.keys():
            print(key)
        
        for key in model_state_dict_0.keys():
            if 'embeddings' in key:
                model_state_dict_.update({key: model_state_dict_0[key]})
            if 'pooler' in key:
                model_state_dict_.update({key: model_state_dict_0[key]})
            if 'layer_ss' in key:
                new_key = key.replace('layer_ss', 'layer')
                model_state_dict_.update({new_key: model_state_dict[key]})
                
                
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(model_state_dict_.keys()):
                if 'classifier' in t or 'cls' in t:
                    del model_state_dict_[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(model_state_dict_.keys()):
                if 'classifier' not in t:
                    del model_state_dict_[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(model_state_dict_.keys()):
                if t not in model_keys:
                    del model_state_dict_[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
        print('*'*77)    
        for key in model_state_dict_.keys():
            print(key)            
            
        model.load_state_dict(model_state_dict_)
        
        
        
        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

def load_model_final_avg(model, layer_initialization, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """
    
    
    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
        model_state_dict = torch.load(checkpoint)
        old_keys = []
        new_keys = []
        for key in model_state_dict.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
        
        model_state_dict_0 = model_state_dict.copy()
        torch.set_printoptions(precision=10)
        
        print("Original model_state_dict")
        for count in range(12):
            L1_norm = 0
            for key in model_state_dict.keys():
                if 'bert.encoder.layer.'+str(count) in key:
                    L1_norm += torch.mean(torch.abs(model_state_dict[key]))
            print("Layer-"+str(count)+" L1 norm : "+str(L1_norm))        
                            
        for count in range(args.student_hidden_layers):
            target_layer = int(layer_initialization[count])-1
            target_layer_ = int(layer_initialization[count])-2
            for key in model_state_dict_0.keys():
                new_key = None
                new_key_ = None
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(target_layer))
                    new_key_ = key.replace(str(count), str(target_layer_))
                    model_state = 0.5*(model_state_dict_0[new_key]+model_state_dict_0[new_key_])
                    model_state_dict.update({key: model_state})
                
        error = 0
        for count in range(args.student_hidden_layers):
            target_layer_num = int(layer_initialization[count])-1
            target_layer_num_ = int(layer_initialization[count])-2
            for key in model_state_dict.keys():
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(target_layer_num))
                    new_key_ = key.replace(str(count), str(target_layer_num_))
                    if torch.mean(torch.abs(model_state_dict[key])) != torch.mean(torch.abs(0.5*(model_state_dict_0[new_key]+model_state_dict_0[new_key_]))):
                        error+=1
                    
        if error != 0:
            print("Error has occured")
        elif error == 0:
            for count in range(args.student_hidden_layers):
                print("Layer "+str(count+1)+" = Original checkpoint's average of "+str(layer_initialization[count])+"-th and "+str(layer_initialization[count]-1)+"-th Layer")
                
        
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(model_state_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(model_state_dict.keys()):
                if 'classifier' not in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(model_state_dict.keys()):
                if t not in model_keys:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
            
            
            
        model.load_state_dict(model_state_dict)
        
        
        
        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

def load_model_final_DT(model, layer_initialization, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """
    
    
    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
        model_state_dict = torch.load(checkpoint)
        old_keys = []
        new_keys = []
        for key in model_state_dict.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
        
        model_state_dict_0 = model_state_dict.copy()
        torch.set_printoptions(precision=10)
        
        print("Original model_state_dict")
        for count in range(12):
            L1_norm = 0
            for key in model_state_dict.keys():
                if 'bert.encoder.layer.'+str(count) in key:
                    L1_norm += torch.mean(torch.abs(model_state_dict[key]))
            print("Layer-"+str(count)+" L1 norm : "+str(L1_norm))        
        
        for i in range(3,9):
            count = 0
            for key in model_state_dict_0.keys():
                new_key = None
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(i))
                    model_state_dict.update({new_key: model_state_dict_0[key]})
        
        model_state_dict_1 = model_state_dict.copy()
        
        for count in range(args.student_hidden_layers):
            target_layer = int(layer_initialization[count])-1
            for key in model_state_dict_1.keys():
                new_key = None
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(target_layer))
                    model_state_dict.update({key: model_state_dict_0[new_key]})
                
        error = 0
        for count in range(args.student_hidden_layers):
            target_layer_num = int(layer_initialization[count])-1
            for key in model_state_dict.keys():
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(target_layer_num))
                    if torch.mean(torch.abs(model_state_dict[key])) != torch.mean(torch.abs(model_state_dict_0[new_key])):
                        error+=1
                    
        if error != 0:
            print("Error has occured")
        elif error == 0:
            for count in range(args.student_hidden_layers):
                print("Layer "+str(count+1)+" = Original checkpoint's "+str(layer_initialization[count])+"-th Layer")
                
        
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(model_state_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(model_state_dict.keys()):
                if 'classifier' not in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(model_state_dict.keys()):
                if t not in model_keys:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
            
            
            
        model.load_state_dict(model_state_dict)
        
        
        
        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

def load_model_NL(model, layer_initialization, checkpoint, args, mode='exact', train_mode='finetune', verbose=True, DEBUG=False):
    """

    :param model:
    :param checkpoint:
    :param argstrain:
    :param mode:  this is created because for old training the encoder and classifier are mixed together
                  also adding student mode
    :param train_mode:
    :param verbose:
    :return:
    """

    n_gpu = args.n_gpu
    device = args.device
    local_rank = -1
    if checkpoint in [None, 'None']:
        if verbose:
            logger.info('no checkpoint provided for %s!' % model._get_name())
    else:
        if not os.path.exists(checkpoint):
            raise ValueError('checkpoint %s not exist' % checkpoint)
        if verbose:
            logger.info('loading %s finetuned model from %s' % (model._get_name(), checkpoint))
        model_state_dict = torch.load(checkpoint)
            
        old_keys = []
        new_keys = []
        for key in model_state_dict.keys():
            new_key = None
            if 'gamma' in key:
                new_key = key.replace('gamma', 'weight')
            if 'beta' in key:
                new_key = key.replace('beta', 'bias')
            if key.startswith('module.'):
                new_key = key.replace('module.', '')
            if new_key:
                old_keys.append(key)
                new_keys.append(new_key)
        for old_key, new_key in zip(old_keys, new_keys):
            model_state_dict[new_key] = model_state_dict.pop(old_key)
                
        model_state_dict_0 = model_state_dict.copy()
        torch.set_printoptions(precision=10)               
        
        # First generate keys for NL layers. The value is changed later below.
        for i in range(3*args.student_hidden_layers):
            count = 0
            for key in model_state_dict_0.keys():
                new_key = None
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(i))
                    model_state_dict.update({new_key: model_state_dict_0[key]})
                
        model_state_dict_1 = model_state_dict.copy()
        
        for count in range(3*args.student_hidden_layers):
            target_layer = int(layer_initialization[count])-1
            for key in model_state_dict_1.keys():
                new_key = None
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(target_layer))
                    model_state_dict.update({key: model_state_dict_0[new_key]})
                
        error = 0
        for count in range(3*args.student_hidden_layers):
            target_layer_num = int(layer_initialization[count])-1
            for key in model_state_dict.keys():
                if 'bert.encoder.layer.'+str(count)+'.' in key:
                    new_key = key.replace(str(count), str(target_layer_num))
                    if torch.mean(torch.abs(model_state_dict[key])) != torch.mean(torch.abs(model_state_dict_0[new_key])):
                        error+=1
                    
        if error != 0:
            print("Error has occured")
        elif error == 0:
            for count in range(3*args.student_hidden_layers):
                print("Layer "+str(count+1)+" = Original checkpoint's "+str(layer_initialization[count])+"-th Layer")
                
        
        del_keys = []
        keep_keys = []
        if mode == 'exact':
            pass
        elif mode == 'encoder':
            for t in list(model_state_dict.keys()):
                if 'classifier' in t or 'cls' in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'classifier':
            for t in list(model_state_dict.keys()):
                if 'classifier' not in t:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        elif mode == 'student':
            model_keys = model.state_dict().keys()
            for t in list(model_state_dict.keys()):
                if t not in model_keys:
                    del model_state_dict[t]
                    del_keys.append(t)
                else:
                    keep_keys.append(t)
        else:
            raise ValueError('%s not available for now' % mode)
            
        model.load_state_dict(model_state_dict)        
        
        if mode != 'exact':
            logger.info('delete %d layers, keep %d layers' % (len(del_keys), len(keep_keys)))
        if DEBUG:
            print('deleted keys =\n {}'.format('\n'.join(del_keys)))
            print('*' * 77)
            print('kept keys =\n {}'.format('\n'.join(keep_keys)))

    if args.fp16:
        logger.info('fp16 activated, now call model.half()')
        model.half()
    model.to(device)

    if train_mode != 'finetune':
        if verbose:
            logger.info('freeze BERT layer in DEBUG mode')
        model.set_mode(train_mode)

    if local_rank != -1:
        raise NotImplementedError('not implemented for local_rank != 1')
    elif n_gpu > 1:
        logger.info('data parallel because more than one gpu')
        model = torch.nn.DataParallel(model)
    return model

def eval_model_dataloader(encoder_bert, classifier, dataloader, device, detailed=False,
                          criterion=nn.CrossEntropyLoss(reduction='sum'), use_pooled_output=True,
                          verbose = False):
    """
    :param encoder_bert:  either a encoder, or a encoder with classifier
    :param classifier:    if a encoder, classifier needs to be provided
    :param dataloader:
    :param device:
    :param detailed:
    :return:
    """
    if hasattr(encoder_bert, 'module'):
        encoder_bert = encoder_bert.module
    if hasattr(classifier, 'module'):
        classifier = classifier.module

    n_layer = len(encoder_bert.bert.encoder.layer)
    encoder_bert.eval()
    if classifier is not None:
        classifier.eval()

    loss = 0
    acc = 0

    # set loss function
    if detailed:
        feature_maps = [[] for _ in range(n_layer)]   # assume we only deal with bert base here
        predictions = []
        pooled_feat_maps = []

    # evaluate network
    # for idx, batch in enumerate(dataloader):
    for idx, batch in enumerate(dataloader):
        batch = tuple(t.to(device) for t in batch)
        if len(batch) > 4:
            input_ids, input_mask, segment_ids, label_ids, *ignore = batch
        else:
            input_ids, input_mask, segment_ids, label_ids = batch

        with torch.no_grad():
            if classifier is None:
                preds = encoder_bert(input_ids, segment_ids, input_mask)
            else:
                feat = encoder_bert(input_ids, segment_ids, input_mask)
                if isinstance(feat, tuple):
                    feat, pooled_feat = feat
                    if use_pooled_output:
                        preds = classifier(pooled_feat)
                    else:
                        preds = classifier(feat)
                else:
                    feat, pooled_feat = None, feat
                    preds = classifier(pooled_feat)
        loss += criterion(preds, label_ids).sum().item()

        pred_cls = preds.data.max(1)[1]
        acc += pred_cls.eq(label_ids).sum().cpu().item()

        if detailed:
            bs = input_ids.shape[0]
            need_reshape = bs != pooled_feat.shape[0]
            if classifier is None:
                raise ValueError('without classifier, feature cannot be calculated')
            if feat is None:
                pass
            else:
                for fm, f in zip(feature_maps, feat):
                    if need_reshape:
                        fm.append(f.contiguous().view(bs, -1).detach().cpu().numpy())
                    else:
                        fm.append(f.detach().cpu().numpy())
            if need_reshape:
                pooled_feat_maps.append(pooled_feat.contiguous().view(bs, -1).detach().cpu().numpy())
            else:
                pooled_feat_maps.append(pooled_feat.detach().cpu().numpy())

            predictions.append(preds.detach().cpu().numpy())
        if verbose:
            logger.info('input_ids.shape = {}, tot_loss = {}, tot_correct = {}'.format(input_ids.shape, loss, acc))

    loss /= len(dataloader.dataset) * 1.0
    acc /= len(dataloader.dataset) * 1.0
    
    if detailed:
        feat_maps = [np.concatenate(t) for t in feature_maps] if len(feature_maps[0]) > 0 else None
        if n_layer == 24:
            return {'loss': loss,
                    'acc': acc,
                    'pooled_feature_maps': np.concatenate(pooled_feat_maps),
                    'pred_logit': np.concatenate(predictions),
                    'feature_maps': [feat_maps[i] for i in [3, 7, 11, 15, 19]]}
        else:
            return {'loss': loss,
                    'acc': acc,
                    'pooled_feature_maps': np.concatenate(pooled_feat_maps),
                    'pred_logit': np.concatenate(predictions),
                    'feature_maps': feat_maps}

    return {'loss': loss, 'acc': acc}

def eval_model_dataloader_sim(encoder_bert, classifier, dataloader, device, detailed=False,
                          criterion=nn.CrossEntropyLoss(reduction='sum'), use_pooled_output=True,
                          verbose = False):
    """
    :param encoder_bert:  either a encoder, or a encoder with classifier
    :param classifier:    if a encoder, classifier needs to be provided
    :param dataloader:
    :param device:
    :param detailed:
    :return:
    """
    if hasattr(encoder_bert, 'module'):
        encoder_bert = encoder_bert.module
    if hasattr(classifier, 'module'):
        classifier = classifier.module

    n_layer = len(encoder_bert.bert.encoder.layer)
    encoder_bert.eval()
    if classifier is not None:
        classifier.eval()

    loss = 0
    acc = 0

    # set loss function
    if detailed:
        feature_maps = [[] for _ in range(n_layer)]   # assume we only deal with bert base here
        Qx_outputs = [[] for _ in range(n_layer)]
        Kx_outputs = [[] for _ in range(n_layer)]
        Vx_outputs = [[] for _ in range(n_layer)]
        predictions = []
        pooled_feat_maps = []

    # evaluate network
    # for idx, batch in enumerate(dataloader):
    for idx, batch in enumerate(dataloader):
        batch = tuple(t.to(device) for t in batch)
        if len(batch) > 4:
            input_ids, input_mask, segment_ids, label_ids, *ignore = batch
        else:
            input_ids, input_mask, segment_ids, label_ids = batch

        with torch.no_grad():
            if classifier is None:
                preds = encoder_bert(input_ids, segment_ids, input_mask)
            else:
                feat = encoder_bert(input_ids, segment_ids, input_mask)
                if isinstance(feat, tuple):
                    feat, pooled_feat, Qx, Kx, Vx = feat
                    if use_pooled_output:
                        preds = classifier(pooled_feat)
                    else:
                        preds = classifier(feat)
                else:
                    feat, pooled_feat = None, feat
                    preds = classifier(pooled_feat)
        loss += criterion(preds, label_ids).sum().item()

        pred_cls = preds.data.max(1)[1]
        acc += pred_cls.eq(label_ids).sum().cpu().item()

        if detailed:
            bs = input_ids.shape[0]
            need_reshape = bs != pooled_feat.shape[0]
            if classifier is None:
                raise ValueError('without classifier, feature cannot be calculated')
            if feat is None:
                pass
            else:
                for fm, f in zip(feature_maps, feat):
                    if need_reshape:
                        fm.append(f.contiguous().view(bs, -1).detach().cpu().numpy())
                    else:
                        fm.append(f.detach().cpu().numpy())
                for fQ, f in zip(Qx_outputs, Qx):
                    if need_reshape:
                        fQ.append(f.contiguous().view(bs, -1).detach().cpu().numpy())
                    else:
                        fQ.append(f.detach().cpu().numpy())
                for fK, f in zip(Kx_outputs, Kx):
                    if need_reshape:
                        fK.append(f.contiguous().view(bs, -1).detach().cpu().numpy())
                    else:
                        fK.append(f.detach().cpu().numpy())
                for fV, f in zip(Vx_outputs, Vx):
                    if need_reshape:
                        fV.append(f.contiguous().view(bs, -1).detach().cpu().numpy())
                    else:
                        fV.append(f.detach().cpu().numpy())                        
            if need_reshape:
                pooled_feat_maps.append(pooled_feat.contiguous().view(bs, -1).detach().cpu().numpy())
            else:
                pooled_feat_maps.append(pooled_feat.detach().cpu().numpy())

            predictions.append(preds.detach().cpu().numpy())
        if verbose:
            logger.info('input_ids.shape = {}, tot_loss = {}, tot_correct = {}'.format(input_ids.shape, loss, acc))

    loss /= len(dataloader.dataset) * 1.0
    acc /= len(dataloader.dataset) * 1.0
    
    if detailed:
        feat_maps = [np.concatenate(t) for t in feature_maps] if len(feature_maps[0]) > 0 else None
        Qx_maps = [np.concatenate(t) for t in Qx_outputs] if len(Qx_outputs[0]) > 0 else None
        Kx_maps = [np.concatenate(t) for t in Kx_outputs] if len(Kx_outputs[0]) > 0 else None
        Vx_maps = [np.concatenate(t) for t in Vx_outputs] if len(Vx_outputs[0]) > 0 else None        
        if n_layer == 24:
            return {'loss': loss,
                    'acc': acc,
                    'pooled_feature_maps': np.concatenate(pooled_feat_maps),
                    'pred_logit': np.concatenate(predictions),
                    'feature_maps': [feat_maps[i] for i in [3, 7, 11, 15, 19]]}
        else:
            return {'loss': loss,
                    'acc': acc,
                    'pooled_feature_maps': np.concatenate(pooled_feat_maps),
                    'pred_logit': np.concatenate(predictions),
                    'feature_maps': feat_maps,
                    'Qx_outputs': Qx_maps,
                    'Kx_outputs': Kx_maps,
                    'Vx_outputs': Vx_maps}

    return {'loss': loss, 'acc': acc}

def run_process(proc):
    os.system(proc)


def eval_model_dataloader_nli(task_name, eval_label_ids, encoder_bert, classifier, dataloader, kd_model, num_labels,
                              device, weights=None, layer_idx=None, output_mode='classification'):
    encoder_bert.eval()
    classifier.eval()

    eval_loss = 0
    nb_eval_steps = 0
    preds = []

    for input_ids, input_mask, segment_ids, label_ids in dataloader:
        input_ids = input_ids.to(device)
        input_mask = input_mask.to(device)
        segment_ids = segment_ids.to(device)
        label_ids = label_ids.to(device)

        with torch.no_grad():
            full_output, pooled_output = encoder_bert(input_ids, segment_ids, input_mask)
            if kd_model.lower() in['kd', 'kd.cls']:
                logits = classifier(pooled_output)
            elif kd_model.lower() == 'kd.full':
                logits = classifier(full_output, weights, layer_idx)
            else:
                raise NotImplementedError(f'{kd_model} not implemented yet')

        # create eval loss and other metric required by the task
        if output_mode == "classification":
            loss_fct = CrossEntropyLoss()
            tmp_eval_loss = loss_fct(logits.view(-1, num_labels), label_ids.view(-1))
        elif output_mode == "regression":
            raise NotImplementedError('regression not implemented yet')

        eval_loss += tmp_eval_loss.mean().item()
        nb_eval_steps += 1
        if len(preds) == 0:
            preds.append(logits.detach().cpu().numpy())
        else:
            preds[0] = np.append(
                preds[0], logits.detach().cpu().numpy(), axis=0)

    eval_loss = eval_loss / nb_eval_steps
    preds = preds[0]
    if output_mode == "classification":
        preds = np.argmax(preds, axis=1).flatten()
    elif output_mode == "regression":
        preds = np.squeeze(preds)
    result = compute_metrics(task_name, preds, eval_label_ids.numpy())
    result['eval_loss'] = eval_loss
    return result

def eval_model_dataloader_nli_NL(task_name, eval_label_ids, encoder_bert, classifier, classifier_2, classifier_3, dataloader, kd_model, num_labels,
                              device, weights=None, layer_idx=None, output_mode='classification', NL_mode = 0):
    encoder_bert.eval()
    classifier.eval()
    classifier_2.eval()
    classifier_3.eval()

    eval_loss = 0
    eval_loss_2 = 0
    eval_loss_3 = 0
    nb_eval_steps = 0
    nb_eval_steps_2 =0 
    nb_eval_steps_3 = 0
    preds = []
    preds_2 = []
    preds_3 = []

    for input_ids, input_mask, segment_ids, label_ids in dataloader:
        input_ids = input_ids.to(device)
        input_mask = input_mask.to(device)
        segment_ids = segment_ids.to(device)
        label_ids = label_ids.to(device)

        with torch.no_grad():
            full_output,full_output_2, full_output_3, pooled_output, pooled_output_2, pooled_output_3 = encoder_bert(input_ids, segment_ids, input_mask, NL_mode = NL_mode)
            if kd_model.lower() in['kd', 'kd.cls']:
                if NL_mode == 0:
                    logits = classifier(pooled_output)
                    logits_2 = classifier_2(pooled_output_2)
                    logits_3 = classifier_3(pooled_output_3)
                elif NL_mode == 1:
                    logits_2 = classifier_2(pooled_output)
                    logits_3 = classifier_3(pooled_output_2)
                elif NL_mode == 2:
                    logits = classifier(pooled_output)
                    logits_3 = classifier_3(pooled_output_3)
                elif NL_mode == 3:
                    logits = classifier(pooled_output)
                    logits_2 = classifier_2(pooled_output_2)                     
            elif kd_model.lower() == 'kd.full':
                logits = classifier(full_output, weights, layer_idx)
            else:
                raise NotImplementedError(f'{kd_model} not implemented yet')

        # create eval loss and other metric required by the task
        if output_mode == "classification":
            loss_fct = CrossEntropyLoss()
            if NL_mode == 0:
                tmp_eval_loss = loss_fct(logits.view(-1, num_labels), label_ids.view(-1))
                tmp_eval_loss_2 = loss_fct(logits_2.view(-1, num_labels), label_ids.view(-1))
                tmp_eval_loss_3 = loss_fct(logits_3.view(-1, num_labels), label_ids.view(-1))
            elif NL_mode == 1:    
                tmp_eval_loss_2 = loss_fct(logits_2.view(-1, num_labels), label_ids.view(-1))
                tmp_eval_loss_3 = loss_fct(logits_3.view(-1, num_labels), label_ids.view(-1))
            elif NL_mode == 2:
                tmp_eval_loss = loss_fct(logits.view(-1, num_labels), label_ids.view(-1))
                tmp_eval_loss_3 = loss_fct(logits_3.view(-1, num_labels), label_ids.view(-1))
            elif NL_mode == 3:
                tmp_eval_loss = loss_fct(logits.view(-1, num_labels), label_ids.view(-1))
                tmp_eval_loss_2 = loss_fct(logits_2.view(-1, num_labels), label_ids.view(-1))                
                                
        elif output_mode == "regression":
            raise NotImplementedError('regression not implemented yet')
        
        if NL_mode == 0:
            eval_loss += tmp_eval_loss.mean().item()
            eval_loss_2 += tmp_eval_loss_2.mean().item()
            eval_loss_3 += tmp_eval_loss_3.mean().item()
            nb_eval_steps += 1
            nb_eval_steps_2 += 1
            nb_eval_steps_3 += 1
        elif NL_mode == 1:
            eval_loss_2 += tmp_eval_loss_2.mean().item()
            eval_loss_3 += tmp_eval_loss_3.mean().item()
            nb_eval_steps_2 += 1
            nb_eval_steps_3 += 1
        elif NL_mode == 2:
            eval_loss += tmp_eval_loss.mean().item()
            eval_loss_3 += tmp_eval_loss_3.mean().item()
            nb_eval_steps += 1
            nb_eval_steps_3 += 1
        elif NL_mode == 3:
            eval_loss += tmp_eval_loss.mean().item()
            eval_loss_2 += tmp_eval_loss_2.mean().item()
            nb_eval_steps += 1
            nb_eval_steps_2 += 1            
        
        if NL_mode == 0:
            if len(preds) == 0:
                preds.append(logits.detach().cpu().numpy())
            else:
                preds[0] = np.append(
                    preds[0], logits.detach().cpu().numpy(), axis=0)
            
            if len(preds_2) == 0:
                preds_2.append(logits_2.detach().cpu().numpy())
            else:
                preds_2[0] = np.append(
                    preds_2[0], logits_2.detach().cpu().numpy(), axis=0)
        
            if len(preds_3) == 0:
                preds_3.append(logits_3.detach().cpu().numpy())
            else:
                preds_3[0] = np.append(
                    preds_3[0], logits_3.detach().cpu().numpy(), axis=0)

        elif NL_mode == 1:
            if len(preds_2) == 0:
                preds_2.append(logits_2.detach().cpu().numpy())
            else:
                preds_2[0] = np.append(
                    preds_2[0], logits_2.detach().cpu().numpy(), axis=0)
            
            if len(preds_3) == 0:
                preds_3.append(logits_3.detach().cpu().numpy())
            else:
                preds_3[0] = np.append(
                    preds_3[0], logits_3.detach().cpu().numpy(), axis=0)

        if NL_mode == 2:
            if len(preds) == 0:
                preds.append(logits.detach().cpu().numpy())
            else:
                preds[0] = np.append(
                    preds[0], logits.detach().cpu().numpy(), axis=0)
            
            if len(preds_3) == 0:
                preds_3.append(logits_3.detach().cpu().numpy())
            else:
                preds_3[0] = np.append(
                    preds_3[0], logits_3.detach().cpu().numpy(), axis=0)
   
        if NL_mode == 3:
            if len(preds) == 0:
                preds.append(logits.detach().cpu().numpy())
            else:
                preds[0] = np.append(
                    preds[0], logits.detach().cpu().numpy(), axis=0)
            
            if len(preds_2) == 0:
                preds_2.append(logits_2.detach().cpu().numpy())
            else:
                preds_2[0] = np.append(
                    preds_2[0], logits_2.detach().cpu().numpy(), axis=0)
                
    if NL_mode == 0:
        eval_loss = eval_loss / nb_eval_steps
        eval_loss_2 = eval_loss_2 / nb_eval_steps_2
        eval_loss_3 = eval_loss_3 / nb_eval_steps_3
        preds = preds[0]
        preds_2 = preds_2[0]
        preds_3 = preds_3[0]
        if output_mode == "classification":
            preds = np.argmax(preds, axis=1).flatten()
            preds_2 = np.argmax(preds_2, axis=1).flatten()
            preds_3 = np.argmax(preds_3, axis=1).flatten()
        elif output_mode == "regression":
            preds = np.squeeze(preds)
            preds_2 = np.squeeze(preds_2)
            preds_3 = np.squeeze(preds_3)
        result = compute_metrics(task_name, preds, eval_label_ids.numpy())
        result_2 = compute_metrics(task_name, preds_2, eval_label_ids.numpy())
        result_3 = compute_metrics(task_name, preds_3, eval_label_ids.numpy())
        
        if task_name.lower() == 'mrpc':
            acc_1 = result['f1']
            acc_2 = result_2['f1']
            acc_3 = result_3['f1']
            result['eval_loss_DT_1'] = eval_loss
            result['eval_loss_Negotiator'] = eval_loss_2
            result['eval_loss_DT_2'] = eval_loss_3
        
            result['acc_DT_1'] = acc_1
            result['acc_Negotiator'] = acc_2
            result['acc_DT_2'] = acc_3
            
            result['acc_and_f1_DT_1'] = result['acc_and_f1']
            result['acc_and_f1_DT_2'] = result_3['acc_and_f1']
            result['acc_and_f1_Negotiator'] = result_2['acc_and_f1']
        elif task_name.lower() =='cola':
            acc_1 = result['mcc']
            acc_2 = result_2['mcc']
            acc_3 = result_3['mcc']
            result['eval_loss_DT_1'] = eval_loss
            result['eval_loss_Negotiator'] = eval_loss_2
            result['eval_loss_DT_2'] = eval_loss_3
        
            result['acc_DT_1'] = acc_1
            result['acc_Negotiator'] = acc_2
            result['acc_DT_2'] = acc_3
        else:
            acc_1 = result['acc']
            acc_2 = result_2['acc']
            acc_3 = result_3['acc']
            result['eval_loss_DT_1'] = eval_loss
            result['eval_loss_Negotiator'] = eval_loss_2
            result['eval_loss_DT_2'] = eval_loss_3
        
            result['acc_DT_1'] = acc_1
            result['acc_Negotiator'] = acc_2
            result['acc_DT_2'] = acc_3
                    
        return result

    elif RT_mode == 1:
        eval_loss_2 = eval_loss_2 / nb_eval_steps
        eval_loss_3 = eval_loss_3 / nb_eval_steps_3
        preds_2 = preds_2[0]
        preds_3 = preds_3[0]
        if output_mode == "classification":
            preds_2 = np.argmax(preds_2, axis=1).flatten()
            preds_3 = np.argmax(preds_3, axis=1).flatten()
        elif output_mode == "regression":
            preds_2 = np.squeeze(preds_2)
            preds_3 = np.squeeze(preds_3)
        result_2 = compute_metrics(task_name, preds_2, eval_label_ids.numpy())
        result_3 = compute_metrics(task_name, preds_3, eval_label_ids.numpy())

        if task_name.lower() == 'mrpc':
            acc_2 = result_2['f1']
            acc_3 = result_3['f1']
            result['eval_loss_Negotiator'] = eval_loss_2
            result['eval_loss_DT_2'] = eval_loss_3
        
            result['acc_Negotiator'] = acc_2
            result['acc_DT_2'] = acc_3
            
            result['acc_and_f1_Negotiator'] = result_2['acc_and_f1']
            result['acc_and_f1_DT_2'] = result_3['acc_and_f1']
            
        elif task_name.lower() == 'cola':
            acc_2 = result_2['mcc']
            acc_3 = result_3['mcc']
            result['eval_loss_Negotiator'] = eval_loss_2
            result['eval_loss_DT_2'] = eval_loss_3
        
            result['acc_Negotiator'] = acc_2
            result['acc_DT_2'] = acc_3
            
        else:
            acc_2 = result_2['acc']
            acc_3 = result_3['acc']
            result['eval_loss_Negotiator'] = eval_loss_2
            result['eval_loss_DT_2'] = eval_loss_3
        
            result['acc_Negotiator'] = acc_2
            result['acc_DT_2'] = acc_3
            
        return result

    elif RT_mode == 2:
        eval_loss_3 = eval_loss_3 / nb_eval_steps
        eval_loss = eval_loss / nb_eval_steps
        preds_3 = preds_3[0]
        preds = preds[0]
        if output_mode == "classification":
            preds_3 = np.argmax(preds_3, axis=1).flatten()
            preds = np.argmax(preds, axis=1).flatten()
        elif output_mode == "regression":
            preds_3 = np.squeeze(preds_3)
            preds = np.squeeze(preds)
        result_3 = compute_metrics(task_name, preds_3, eval_label_ids.numpy())
        result = compute_metrics(task_name, preds, eval_label_ids.numpy())
        
        if task_name.lower() == 'mrpc':
            acc_3 = result_3['f1']
            acc = result['f1']
            result['eval_loss_DT_2'] = eval_loss_3
            result['eval_loss_DT_1'] = eval_loss
            
            result['acc_DT_2'] = acc_3
            result['acc_DT_1'] = acc
            
            result['acc_and_f1_DT_2'] = result_3['acc_and_f1']
            result['acc_and_f1_DT_1'] = result['acc_and_f1']
            
        elif task_name.lower() == 'cola':
            acc_3 = result_3['mcc']
            acc = result['mcc']
            result['eval_loss_DT_2'] = eval_loss_3
            result['eval_loss_DT_1'] = eval_loss
            
            result['acc_DT_2'] = acc_3
            result['acc_DT_1'] = acc
            
        else:
            acc_3 = result_3['acc']
            acc = result['acc']
            result['eval_loss_DT_2'] = eval_loss_3
            result['eval_loss_DT_1'] = eval_loss
            
            result['acc_DT_2'] = acc_3
            result['acc_DT_1'] = acc
                    
        return result

    elif RT_mode == 3:
        eval_loss = eval_loss / nb_eval_steps
        eval_loss_2 = eval_loss_2 / nb_eval_steps_2
        preds = preds[0]
        preds_2 = preds_2[0]
        if output_mode == "classification":
            preds = np.argmax(preds, axis=1).flatten()
            preds_2 = np.argmax(preds_2, axis=1).flatten()
        elif output_mode == "regression":
            preds = np.squeeze(preds)
            preds_2 = np.squeeze(preds_2)
        result = compute_metrics(task_name, preds, eval_label_ids.numpy())
        result_2 = compute_metrics(task_name, preds_2, eval_label_ids.numpy())
        
        if task_name.lower() == 'mrpc':
            acc_1 = result['f1']
            acc_2 = result_2['f1']
            result['eval_loss_DT_1'] = eval_loss
            result['eval_loss_Negotiator'] = eval_loss_2
            
            result['acc_DT_1'] = acc_1
            result['acc_Negotiator'] = acc_2
            
            result['acc_and_f1_DT_1'] = result['acc_and_f1']
            result['acc_and_f1_Negotiator'] = result_2['acc_and_f1']

        if task_name.lower() == 'cola':
            acc_1 = result['mcc']
            acc_2 = result_2['mcc']
            result['eval_loss_DT_1'] = eval_loss
            result['eval_loss_Negotiator'] = eval_loss_2
            
            result['acc_DT_1'] = acc_1
            result['acc_Negotiator'] = acc_2

        else:
            acc_1 = result['acc']
            acc_2 = result_2['acc']
            result['eval_loss_DT_1'] = eval_loss
            result['eval_loss_Negotiator'] = eval_loss_2
            
            result['acc_DT_1'] = acc_1
            result['acc_Negotiator'] = acc_2
            
        return result