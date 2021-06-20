"""
File used to specify important hyperparameters during training. Mainly based on [GitHub repository](https://github.com/intersun/PKD-for-BERT-Model-Compression) for [Patient Knowledge Distillation for BERT Model Compression](https://arxiv.org/abs/1908.09355).
"""
import argparse
import logging
import os
import random
import torch

from envs import HOME_DATA_FOLDER, HOME_OUTPUT_FOLDER

logger = logging.getLogger(__name__)


def boolean_string(s):
    if s not in {'False', 'True'}:
        raise ValueError('Not a valid boolean string')
    return s == 'True'


def is_folder_empty(folder_name):
    if len([f for f in os.listdir(folder_name) if not f.startswith('.')]) == 0:
        return True
    else:
        return False


def default_parser():
    parser = argparse.ArgumentParser()

    # Input Training tasks
    parser.add_argument("--task_name",
                        default=None,
                        type=str,
                        help="The name of the task for training.")

    # System related parameters
#     parser.add_argument("--output_dir",
#                         default=os.path.join(HOME_DATA_FOLDER, 'outputs'),
#                         type=str,
#                         help="The output directory where the model checkpoints will be written.")
    parser.add_argument("--output_dir",
                        default = None,
                        type = str,
                        help="The output directory where the model checkpoints will be written.")
    parser.add_argument("--log_every_step",
                        default=1,
                        type=int,
                        help="output to log every global x training steps, default is 1")

    parser.add_argument("--max_seq_length",
                        default=512,
                        type=int,
                        help="The maximum total input sequence length after WordPiece tokenization. \n"
                             "Sequences longer than this will be truncated, and sequences shorter \n"
                             "than this will be padded.")

    # Training related parameters
    parser.add_argument('--task',
                        type=str,
                        default='MRPC',
                        help="The task you want to finetune the model on.")
    parser.add_argument('--train_type',
                        type=str,
                        default=None,
                        help="train type: one of ft, kd, pkd")
    parser.add_argument('--model_type',
                        type=str,
                        default='Original',
                        help="Model type: one of Original or SPS") 
    parser.add_argument('--train_seed',
                        type=int,
                        default=None,
                        help="random seed for training")
    parser.add_argument('--NL_mode',
                        type=int,
                        default=None,
                        help="NL mode")
    parser.add_argument('--CS_mode',
                        type=int,
                        default=None,
                        help="CS mode")    
    parser.add_argument('--saving_criterion_acc',
                        type=float,
                        default=1.0,
                        help="If the model's val accuracy is above this value, we save the model.")
    parser.add_argument('--saving_criterion_loss',
                        type=float,
                        default=0.0,
                        help="If the model's val loss is lower than this value, we save the model.")
    parser.add_argument('--load_model_dir',
                        type =str,
                        default = None,
                        help="Load model")
    parser.add_argument('--save_model_dir',
                        type = str,
                        default = None,
                        help="Specify the directory where to save the final model")
    parser.add_argument('--seed',
                        type=int,
                        default=None,
                        help="random seed for initialization")
    parser.add_argument("--train_batch_size",
                        default=None,
                        type=int,
                        help="Total batch size for training.")
    parser.add_argument("--eval_batch_size",
                        default=None,
                        type=int,
                        help="Total batch size for eval.")
    parser.add_argument("--learning_rate",
                        default=2e-5,
                        type=float,
                        help="The initial learning rate for Adam.")
    parser.add_argument("--num_train_epochs",
                        default=10.0,
                        type=float,
                        help="Total number of training epochs to perform.")
    parser.add_argument('--gradient_accumulation_steps',
                        type=int,
                        default=1,
                        help="Number of updates steps to accumulate before performing a backward/update pass.")
    parser.add_argument('--fp16',
                        type=boolean_string,
                        default=False,
                        help="Whether to use 16-bit float precision instead of 32-bit")
    parser.add_argument('--loss_scale',
                        type=float, default=0,
                        help="Loss scaling to improve fp16 numeric stability. Only used when fp16 set to True.\n"
                             "0 (default value): dynamic loss scaling.\n"
                             "Positive power of 2: static loss scaling value.\n")
    parser.add_argument('--student_hidden_layers',
                        type=int,
                        default=None,
                        help="number of transformer layers for student, default is None (use all layers)")
    parser.add_argument('--layer_initialization',
                        type=str,
                        default=None,
                        help="Determines how to initialize the student layers")
    parser.add_argument('--freeze_layer',
                        type=list,
                        default=None,
                        help="Determines which layers to freeze")
    parser.add_argument('--teacher_prediction',
                        type=str,
                        default=None,
                        help="teacher prediction file to guild the student's output")
    parser.add_argument("--warmup_proportion",
                        default=0.1,
                        type=float,
                        help="Proportion of training to perform linear learning rate warmup for. "
                             "E.g., 0.1 = 10%% of training.")
    # Distillation related parameters
    parser.add_argument("--bert_model",
                        default=None,
                        type=str,
                        help="student bert model configuration folder")

    parser.add_argument("--encoder_checkpoint",
                        default=None,
                        type=str,
                        help="check point for student encoder")
    parser.add_argument("--cls_checkpoint",
                        default=None,
                        type=str,
                        help="check point for student classifier")
    parser.add_argument("--output_all_encoded_layers",
                        default=False,
                        type=bool,
                        help="if output all encoded layers")
    parser.add_argument("--alpha",
                        default=0.95,
                        type=float,
                        help="alpha for distillation")
    parser.add_argument("--T",
                        default=10.,
                        type=float,
                        help="temperature for distillation")
    parser.add_argument("--beta",
                        default=0.0,
                        type=float,
                        help="weight for AT loss")
    parser.add_argument("--kd_model",
                        default="kd",
                        type=str,
                        help="KD model architecture, either kd, kd.full or kd.cls")
    parser.add_argument("--fc_layer_idx",
                        default=None,
                        type=str,
                        help="layers ids we will put FC layers on, only avaiable when kd_model is kd.full")
    parser.add_argument("--weights",
                        default=None,
                        type=str,
                        help="weight of each layer that we will put FC layers on, only available when kd_model is kd.full")
    parser.add_argument("--normalize_patience",
                        default=False,
                        type=boolean_string,
                        help="normalize patience or not")
    # Distillation related parameters
    parser.add_argument("--do_train",
                        default=False,
                        type=boolean_string,
                        help="do training or not")

    parser.add_argument("--do_eval",
                        default=False,
                        type=boolean_string,
                        help="do evaluation during training or not")
    return parser


def complete_argument(args, out_dir, load_dir = None):
    MODEL_FOLDER = os.path.join(HOME_DATA_FOLDER, 'models')
    if args.student_hidden_layers in [None, 'None']:
        args.student_hidden_layers = 12 if 'base' in args.bert_model else 24
    args.bert_model = os.path.join(MODEL_FOLDER, 'pretrained', args.bert_model)

    if args.encoder_checkpoint is None:
        args.encoder_checkpoint = os.path.join(MODEL_FOLDER, 'pretrained', args.bert_model, 'pytorch_model.bin')
        logger.info('encoder checkpoint not provided, use pre-trained at %s instead' % args.encoder_checkpoint)
        
    if args.cls_checkpoint not in [None, 'None']:
        args.cls_checkpoint = os.path.join(MODEL_FOLDER, args.cls_checkpoint)

    if out_dir is None:
        raise ValueError("Must specify the output directory where the results will be written and saved")
    else:   
        args.output_dir = os.path.join(HOME_OUTPUT_FOLDER, args.task_name)
        args.output_dir = os.path.join(args.output_dir, out_dir)
    
    os.makedirs(args.output_dir, exist_ok=True)
    if load_dir is not None:
        args.load_model_dir = os.path.join(HOME_OUTPUT_FOLDER, args.task_name)
        args.load_model_dir = os.path.join(args.load_model_dir, load_dir)

    if args.task_name == 'MNLI':
        args.output_dir_mm = args.output_dir.replace('MNLI', 'MNLI-mm', 100)
        os.makedirs(args.output_dir_mm, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    n_gpu = torch.cuda.device_count()

    args.device = device
    args.n_gpu = n_gpu
    logger.info("device: {} n_gpu: {}, 16-bits training: {}".format(device, n_gpu, args.fp16))

    if args.train_seed is None:
        args.train_seed = random.randint(0, 100000000)

    return args


def get_predefine_argv(args, mode='glue', task_name='RTE', train_type='kd', student_layers = 3):
    """
    the function return some pre-defined arguments for argument parser
    :param mode:  can only be 'glue' for now
    :param task_name:  one of the task name under glue
    :param train_type: could be 'finetune', 'kd' or 'kd.cls'
    :return:
    """
    if mode == 'race':
        raise NotImplementedError('Please run glue for now')
    elif mode == 'glue':
        argv = [
                '--task_name', task_name,
                '--bert_model', 'bert-base-uncased',
                '--max_seq_length', '128',
                '--train_batch_size', '32',
                '--learning_rate', '2e-5',
                '--num_train_epochs', '10',
                '--eval_batch_size', '32',
                '--gradient_accumulation_steps', '1',
                '--log_every_step', '1',
                '--do_train', 'True',
                '--do_eval', 'True',
                '--fp16', 'False',
            ]
        if train_type == 'ft':
            argv += [
                '--student_hidden_layers', str(student_layers),
                '--train_batch_size', '32',
                '--kd_model', 'kd',
                '--alpha', '0.0',    # alpha = 0 is equivalent to fine-tuning for KD
            ]
        if train_type == 'finetune_student':
            argv += [
                '--student_hidden_layers', str(student_layers),
                '--kd_model', 'kd',
                '--alpha', '0.0',
            ]
        elif train_type == 'kd':
            teacher_pred = HOME_OUTPUT_FOLDER+f'/{task_name}/{task_name}_patient_kd_teacher_12layer_result_summary.pkl'
            argv += [
                '--student_hidden_layers', str(student_layers),
                '--kd_model', 'kd',
                '--alpha', '0.7',
                '--T', '10',
                '--teacher_prediction', teacher_pred,
            ]
        elif train_type == 'pkd':
            teacher_pred = HOME_OUTPUT_FOLDER+f'/{task_name}/{task_name}_patient_kd_teacher_12layer_result_summary.pkl'
            argv += [
                '--student_hidden_layers', str(student_layers),
                '--kd_model', 'kd.cls',
                '--alpha', '0.7',
                '--beta', '100',
                '--T', '10',
                '--teacher_prediction', 
                teacher_pred,
                '--fc_layer_idx', '3,7',   # this for pkd-skip
                '--normalize_patience', 'True',
            ]
    else:
        raise NotImplementedError('training mode %s has not been implemented yet' % mode)
    return argv
