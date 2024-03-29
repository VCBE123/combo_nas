""" Architect controls architecture of cell by computing gradients of alphas """
import copy
import torch
from ...core.nas_modules import NASModule

class DARTSArchitect():
    """ Compute gradients of alphas """
    def __init__(self, config, net):
        """
        Args:
            net
            w_momentum: weights momentum
        """
        self.net = net
        self.v_net = copy.deepcopy(net)
        self.w_momentum = config.w_momentum
        self.w_weight_decay = config.w_weight_decay

    def virtual_step(self, trn_X, trn_y, lr, w_optim):
        """
        Compute unrolled weight w' (virtual step)

        Step process:
        1) forward
        2) calc loss
        3) compute gradient (by backprop)
        4) update gradient

        Args:
            lr: learning rate for virtual gradient step (same as weights lr)
            w_optim: weights optimizer
        """
        # forward & calc loss
        loss = self.net.loss(trn_X, trn_y) # L_trn(w)
        # compute gradient
        gradients = torch.autograd.grad(loss, self.net.weights())
        # do virtual step (update gradient)
        # below operations do not need gradient tracking
        with torch.no_grad():
            # dict key is not the value, but the pointer. So original network weight have to
            # be iterated also.
            for w, vw, g in zip(self.net.weights(), self.v_net.weights(), gradients):
                m = w_optim.state[w].get('momentum_buffer', 0.) * self.w_momentum
                vw.copy_(w - lr * (m + g + self.w_weight_decay*w))
            # synchronize alphas
            for a, va in zip(self.net.alphas(), self.v_net.alphas()):
                va.copy_(a)

    def step(self, trn_X, trn_y, val_X, val_y, lr, w_optim, a_optim):
        """ Compute unrolled loss and backward its gradients
        Args:
            lr: learning rate for virtual gradient step (same as net lr)
            w_optim: weights optimizer - for virtual step
        """
        # do virtual step (calc w`)
        self.virtual_step(trn_X, trn_y, lr, w_optim)
        # calc unrolled loss
        loss = self.v_net.loss(val_X, val_y) # L_val(w`)
        # compute gradient
        v_alphas = tuple(self.v_net.alphas())
        v_weights = tuple(self.v_net.weights())
        v_grads = torch.autograd.grad(loss, v_alphas + v_weights)
        dalpha = v_grads[:len(v_alphas)]
        dw = v_grads[len(v_alphas):]
        hessian = self.compute_hessian(dw, trn_X, trn_y)
        # update final gradient = dalpha - lr*hessian
        with torch.no_grad():
            for alpha, da, h in zip(self.net.alphas(), dalpha, hessian):
                alpha.grad = da - lr*h
        a_optim.step()

    def compute_hessian(self, dw, trn_X, trn_y):
        """
        dw = dw` { L_val(w`, alpha) }
        w+ = w + eps * dw
        w- = w - eps * dw
        hessian = (dalpha { L_trn(w+, alpha) } - dalpha { L_trn(w-, alpha) }) / (2*eps)
        eps = 0.01 / ||dw||
        """
        norm = torch.cat([w.view(-1) for w in dw]).norm()
        eps = 0.01 / norm
        # w+ = w + eps*dw`
        with torch.no_grad():
            for p, d in zip(self.net.weights(), dw):
                p += eps * d
        loss = self.net.loss(trn_X, trn_y)
        dalpha_pos = torch.autograd.grad(loss, self.net.alphas()) # dalpha { L_trn(w+) }
        # w- = w - eps*dw`
        with torch.no_grad():
            for p, d in zip(self.net.weights(), dw):
                p -= 2. * eps * d
        loss = self.net.loss(trn_X, trn_y)
        dalpha_neg = torch.autograd.grad(loss, self.net.alphas()) # dalpha { L_trn(w-) }
        # recover w
        with torch.no_grad():
            for p, d in zip(self.net.weights(), dw):
                p += eps * d
        hessian = [(p-n) / 2.*eps for p, n in zip(dalpha_pos, dalpha_neg)]
        return hessian


class BinaryGateArchitect():
    """ Compute gradients of alphas """
    def __init__(self, config, net):
        """
        Args:
            net
            w_momentum: weights momentum
        """
        self.net = net
        self.n_samples = config.n_samples
        self.sample = (self.n_samples!=0)
        self.renorm = config.renorm and self.sample

    def step(self, trn_X, trn_y, val_X, val_y, lr, w_optim, a_optim):
        """ Compute unrolled loss and backward its gradients
        Args:
            lr: learning rate for virtual gradient step (same as net lr)
            w_optim: weights optimizer - for virtual step
        """
        # sample k
        if self.sample:
            NASModule.param_module_call('sample_ops', n_samples=self.n_samples)
        # loss
        loss = self.net.loss(val_X, val_y)
        # backward
        NASModule.backward_all(loss)
        # renormalization
        if not self.renorm:
            a_optim.step()
        else:
            prev_pw = []
            for p, m in NASModule.param_modules():
                s_op = m.get_state('s_op')
                pdt = p.detach()
                pp = pdt.index_select(-1,s_op)
                if pp.size() == pdt.size(): continue
                k = torch.sum(torch.exp(pdt)) / torch.sum(torch.exp(pp)) - 1
                prev_pw.append(k)

            a_optim.step()

            for kprev, (p, m) in zip(prev_pw, NASModule.param_modules()):
                s_op = m.get_state('s_op')
                pdt = p.detach()
                pp = pdt.index_select(-1,s_op)
                k = torch.sum(torch.exp(pdt)) / torch.sum(torch.exp(pp)) - 1
                for i in s_op:
                    p[i] += (torch.log(k) - torch.log(kprev))

        NASModule.module_call('reset_ops')


class DummyArchitect():
    def __init__(self, config, net):
        sefl.net = net
    
    def step(self, trn_X, trn_y, val_X, val_y, lr, w_optim, a_optim):
        loss = self.net.loss(val_X, val_y)
        loss.backward()
        a_optim.step()