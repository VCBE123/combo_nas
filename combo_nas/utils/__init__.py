# -*- coding: utf-8 -*-
import os
import time
import logging
import numpy as np
import torch
import torch.nn as nn
from .BoT import *

def get_current_device():
    if not torch.cuda.is_available(): return 'cpu'
    return torch.cuda.current_device()

def warmup_device(model, batch_size, device):
    X = torch.randn(batch_size,3,32,32).to(device=device)
    model.train(True)
    o = model(X)
    y = torch.rand(o.size()).to(device=device)
    (o-y).norm().backward()
    model.zero_grad()
    if torch.cuda.is_available():
        torch.cuda.synchronize()

def parse_gpus(gpus):
    if gpus == 'cpu':
        return []
    if gpus == 'all':
        return list(range(torch.cuda.device_count()))
    else:
        return [int(s) for s in gpus.split(',')]

def check_config(hp, name):
    required = (
        'search.data.type',
        'augment.data.type',
        'search.data.train_root',
        'search.data.valid_root',
        'augment.data.train_root',
        'augment.data.valid_root',
    )
    
    flag = False
    for i in required:
        try:
            ddict = hp
            for a in i.split('.'):
                ddict = getattr(ddict, a)
            if ddict == '':
                logging.error('check_config: field {} requires non-empty value'.format(i))
                flag = True
        except:
            logging.error('check_config: field {} is missing'.format(i))
            flag = True

    defaults = {
        'search.data.dloader.cutout': 0,
        'augment.data.dloader.cutout': 16,
        'search.data.dloader.jitter': True,
        'augment.data.dloader.jitter': True,
        'search.plot': False,
        'search.aux_weight': 0.0,
        'augment.aux_weight': 0.0,
        'ops.ops_order': 'act_weight_bn',
        'ops.sepconv_stack': False,
        'ops.affine': False,
        'log.writer': False,
    }

    for i in defaults:
        ddict = hp
        try:
            for a in i.split('.'):
                ddict = getattr(ddict, a)
        except:
            if a != i.split('.')[-1]:
                flag = True
                continue
            else:
                logging.warning('check_config: setting field {} to default: {}'.format(i, defaults[i]))
                setattr(ddict, a, defaults[i])
    
    if flag:
        return True

    hp.search.path = os.path.join('searchs', name)
    hp.search.plot_path = os.path.join(hp.search.path, 'plot')
    logging.info('check_config: OK')
    return False


def init_device(config, ovr_gpus):
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    if not ovr_gpus:
        config.gpus = parse_gpus(config.gpus)
    else:
        config.gpus = parse_gpus(ovr_gpus)
    if len(config.gpus)==0:
        device = torch.device('cpu')
        return device, []
    device = torch.device("cuda")
    torch.cuda.set_device(config.gpus[0])
    
    torch.cuda.manual_seed_all(config.seed)
    torch.backends.cudnn.benchmark = True
    logging.debug('device: {} {}'.format(device, config.gpus))
    return device, config.gpus


def get_logger(log_dir, name):
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    log_path = os.path.join(log_dir, '%s-%d.log' % (name, time.time()))
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(message)s',
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(name)


class DummyWriter():
    def __init__(self):
        pass

    def add_scalar(*args, **kwargs):
        pass
    
    def add_text(*args, **kwargs):
        pass


def get_writer(log_dir, enabled):
    if enabled:
        from tensorboardX import SummaryWriter
        writer = SummaryWriter(log_dir)
    else:
        writer = DummyWriter()
    return writer

def get_net_crit(config):
    crit_type = config.type
    if crit_type == 'LS':
        crit = CrossEntropyLossLS(config.eta)
    else:
        crit = nn.CrossEntropyLoss()
    return crit

def get_optim(params, config):
    if config.type == 'adam':
        optimizer = torch.optim.Adam(params,
                            lr=config.lr,
                            betas=config.betas)
    elif config.type == 'adabound':
        import adabound
        optimizer = adabound.AdaBound(params,
                            lr=config.lr,
                            final_lr=config.final_lr)
    elif config.type == 'sgd':
        optimizer = torch.optim.SGD(params,
                            lr=config.lr,
                            momentum=config.momentum,
                            weight_decay=config.weight_decay,
                            nesterov=config.nesterov)
    else:
        raise Exception("Optimizer not supported: %s" % config.optimizer)
    return optimizer

def get_lr_scheduler(w_optim, config, epochs, last_epoch=-1):
    lr_type = config.type
    lr_args = config.get('args', {})
    if lr_type == 'cosine':
        lr_scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(w_optim, T_max=epochs, **lr_args)
    elif lr_type == 'step':
        lr_scheduler = torch.optim.lr_scheduler.StepLR(w_optim, **lr_args)
    elif lr_type == 'multistep':
        lr_scheduler = torch.optim.lr_scheduler.MultiStepLR(w_optim, **lr_args)
    else:
        raise ValueError('unsupported lr scheduler: {}'.format(lr_type))
    return lr_scheduler

def get_same_padding(kernel_size):
    if isinstance(kernel_size, tuple):
        assert len(kernel_size) == 2, 'invalid kernel size: %s' % kernel_size
        p1 = get_same_padding(kernel_size[0])
        p2 = get_same_padding(kernel_size[1])
        return p1, p2
    assert isinstance(
        kernel_size, int), 'kernel size should be either `int` or `tuple`'
    assert kernel_size % 2 > 0, 'kernel size should be odd number'
    return kernel_size // 2

def param_size(model):
    """ Compute parameter size in MB """
    n_params = sum(
        np.prod(v.size()) for k, v in model.named_parameters() if not k.startswith('aux_head'))
    return 4 * n_params / 1024. / 1024.

def param_count(model):
    """ Compute parameter count in million """
    n_params = sum([p.data.nelement() for p in model.parameters()])
    return n_params / 1e6

class AverageMeter():
    """ Computes and stores the average and current value """
    def __init__(self):
        self.reset()

    def reset(self):
        """ Reset all statistics """
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        """ Update statistics """
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def accuracy(output, target, topk=(1,)):
    """ Computes the precision@k for the specified values of k """
    maxk = max(topk)
    batch_size = target.size(0)

    _, pred = output.topk(maxk, 1, True, True)
    pred = pred.t()
    # one-hot case
    if target.ndimension() > 1:
        target = target.max(1)[1]

    correct = pred.eq(target.view(1, -1).expand_as(pred))

    res = []
    for k in topk:
        correct_k = correct[:k].view(-1).float().sum(0)
        res.append(correct_k.mul_(1.0 / batch_size))

    return res

def format_time(sec):
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return "%d h %d m %d s" % (h,m,s)

class ETAMeter():
    def __init__(self, tot_epochs, epoch, tot_step):
        self.tot_epochs = tot_epochs
        self.epoch = epoch
        self.tot_step = tot_step
    
    def start(self):
        self.last_step = -1
        self.last_time = time.time()

    def step(self, step):
        elps = time.time() - self.last_time
        eta = ((self.tot_epochs-self.epoch) * self.tot_step - step) * elps / (step-self.last_step)
        self.last_time = time.time()
        self.last_step = step
        return eta