from .gradient_based import DARTSArchitect, BinaryGateArchitect
from .reinforce import REINFORCE
from ...utils.registration import Registry, build, get_builder, register, register_wrapper
from functools import partial

arch_optim_registry = Registry('arch_optim')
register_arch_optim = partial(register, arch_optim_registry)
get_arch_optim_builder = partial(get_builder, arch_optim_registry)
build_arch_optim = partial(build, arch_optim_registry)
register = partial(register_wrapper, arch_optim_registry)

register_arch_optim(DARTSArchitect, 'DARTS')
register_arch_optim(BinaryGateArchitect, 'BinGate')
register_arch_optim(REINFORCE, 'REINFORCE')