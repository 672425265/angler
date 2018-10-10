import sys
sys.path.append(".")

from adjoint import gradient

import numpy as np
import copy
import progressbar
import matplotlib.pylab as plt
from scipy.optimize import minimize, fmin_l_bfgs_b
from autograd import grad

class Optimization():

    def __init__(self, J=None, Nsteps=100, eps_max=5, field_start='linear', nl_solver='newton',
                 max_ind_shift=None):

        self._J = J
        self.Nsteps = Nsteps
        self.eps_max = eps_max
        self.field_start = field_start
        self.nl_solver = nl_solver
        self.max_ind_shift = max_ind_shift
        self.src_amplitudes = []
        self.objfn_list = []

        # compute the jacobians of J and store these
        self.dJ = self._autograd_dJ(J)

    def __repr__(self):
        return "Optimization(Nsteps={}, eps_max={}, J={}, field_start={}, nl_solver={})".format(
            self.Nsteps, self.eps_max, self.J, self.field_start, self.nl_solver)

    def __str__(self):
        return self.__repr__()

    @property
    def J(self):
        return self._J

    @J.setter
    def J(self, J):
        self._J = J
        self.dJ = self._autograd_dJ(J)

    def _autograd_dJ(self, J):
        """ Uses autograd to automatically compute Jacobians of J with respect to each argument"""

        # note: eventually want to check whether J has eps_nl argument, then switch between linear and nonlinear depending.
        dJ = {}
        dJ['lin'] = grad(J, 0)
        dJ['nl']  = grad(J, 1)
        dJ['eps'] = grad(J, 2)
        return dJ

    def compute_J(self, simulation):
        """ Returns the current objective function of a simulation"""

        if simulation.fields['Ez'] is None:
            (_, _, Ez) = simulation.solve_fields()
        else:
            Ez = simulation.fields['Ez']

        if simulation.fields_nl['Ez'] is None:  
            (_, _, Ez_nl, _) = simulation.solve_fields_nl()
        else:
            Ez_nl = simulation.fields_nl['Ez']

        eps = simulation.eps_r
        return self.J(Ez, Ez_nl, eps)

    def compute_dJ(self, simulation, design_region):
        """ Returns the current gradient of a simulation"""

        if simulation.fields['Ez'] is None:
            (_, _, Ez) = simulation.solve_fields()
        else:
            Ez = simulation.fields['Ez']

        if simulation.fields_nl['Ez'] is None:  
            (_, _, Ez_nl, _) = simulation.solve_fields_nl()
        else:
            Ez_nl = simulation.fields_nl['Ez']

        arguments = (Ez, Ez_nl, simulation.eps_r)
        return gradient(simulation, self.dJ, design_region, arguments)

    def _set_design_region(self, x, simulation, design_region):
        """ Inserts a vector x into the design_region of simulation.eps_r"""

        eps_vec = copy.deepcopy(np.ndarray.flatten(simulation.eps_r))
        des_vec = np.ndarray.flatten(design_region)

        # Only update the permittivity if it actually differs from the current one 
        # If it doesn't, we don't want to erase the stored fields

        if np.linalg.norm(x - eps_vec[des_vec == 1])/np.linalg.norm(x) > 1e-10:
            eps_vec[des_vec == 1] = x
            eps_new = np.reshape(eps_vec, simulation.eps_r.shape)
            simulation.eps_r = eps_new

    def _get_design_region(self, spatial_array, design_region):
        """ Returns a vector of the elements of spatial_array that are in design_region"""

        spatial_vec = copy.deepcopy(np.ndarray.flatten(spatial_array))
        des_vec = np.ndarray.flatten(design_region)
        x = spatial_vec[des_vec == 1]
        return x

    def _make_progressbar(self, N):
        """ Returns a progressbar to use during optimization"""

        if self.max_ind_shift is not None:

            bar = progressbar.ProgressBar(widgets=[
                ' ', progressbar.DynamicMessage('ObjectiveFn'),
                ' ', progressbar.DynamicMessage('ObjectiveFn_Normalized'),
                ' Iteration: ',
                ' ', progressbar.Counter(), '/%d' % N,
                ' ', progressbar.AdaptiveETA(),
            ], max_value=N)

        else:

            bar = progressbar.ProgressBar(widgets=[
                ' ', progressbar.DynamicMessage('ObjectiveFn'),
                ' Iteration: ',
                ' ', progressbar.Counter(), '/%d' % N,
                ' ', progressbar.AdaptiveETA(),
            ], max_value=N)

        return bar

    def _update_progressbar(self, pbar, iteration, J):

        if self.max_ind_shift is not None:
            objfn_norm = J/np.max(np.square(np.abs(self.simulation.src)))
            pbar.update(iteration, ObjectiveFn=J, ObjectiveFn_Normalized=objfn_norm)
        else:
            pbar.update(iteration, ObjectiveFn=J)

    def run(self, simulation, design_region, method='LBFGS', step_size=0.1,
            beta1=0.9, beta2=0.999, verbose=True):
        """ Runs an optimization."""

        self.simulation = simulation
        self.design_region = design_region
        self.verbose = verbose

        allowed = ['LBFGS', 'GD', 'ADAM']

        if method.lower() in ['lbfgs']:
            self._run_LBFGS()

        elif method.lower() == 'gd':
            self._run_GD(step_size=step_size)

        elif method.lower() == 'adam':
            self._run_ADAM(step_size=step_size, beta1=beta1, beta2=beta2)

        else:
            raise ValueError("'method' must be in {}".format(allowed))

    def _run_GD(self, step_size):
        """ Performs simple gradient descent optimization"""

        pbar = self._make_progressbar(self.Nsteps)

        for iteration in range(self.Nsteps):

            J = self.compute_J(self.simulation)
            self.objfn_list.append(J)
            # pbar.update(iteration, ObjectiveFn=J)
            self._update_progressbar(pbar, iteration, J)

            self._set_source_amplitude()

            gradient = self.compute_dJ(self.simulation, self.design_region)

            self._update_permittivity(gradient, step_size)

    def _run_ADAM(self, step_size, beta1, beta2):
        """ Performs simple gradient descent optimization"""

        pbar = self._make_progressbar(self.Nsteps)

        for iteration in range(self.Nsteps):

            J = self.compute_J(self.simulation)
            self.objfn_list.append(J)
            # pbar.update(iteration, ObjectiveFn=J)
            self._update_progressbar(pbar, iteration, J)

            self._set_source_amplitude()

            gradient = self.compute_dJ(self.simulation, self.design_region)

            if iteration == 0:
                mopt = np.zeros(gradient.shape)
                vopt = np.zeros(gradient.shape)

            (gradient_adam, mopt, vopt) = self._step_adam(gradient, mopt, vopt, iteration, beta1, beta2,)

            self._update_permittivity(gradient_adam, step_size)

    def _run_LBFGS(self):
        """Performs L-BFGS Optimization of objective function w.r.t. eps_r"""

        pbar = self._make_progressbar(self.Nsteps)

        def _objfn(x, *argv):
            """ Returns objective function given some permittivity distribution"""
            self._set_design_region(x, self.simulation, self.design_region)
            J = self.compute_J(self.simulation)

            # return minus J because we technically will minimize
            return -J

        def _grad(x,  *argv):
            """ Returns full gradient given some permittivity distribution"""
            # make a simulation copy
            self._set_design_region(x, self.simulation, self.design_region)

            # compute gradient, extract design region, turn into vector, return
            gradient = self.compute_dJ(self.simulation, self.design_region)
            gradient_vec = self._get_design_region(gradient, self.design_region)

            return -gradient_vec

        # this simple callback function gets run each iteration
        # keeps track of the current iteration step for the progressbar
        # also resets eps on the simulation
        iter_list = [0]

        def _update_iter_count(x_current):
            J = self.compute_J(self.simulation)
            # pbar.update(iter_list[0], ObjectiveFn=J)
            self._update_progressbar(pbar, iter_list[0], J)

            iter_list[0] += 1
            self.objfn_list.append(J)
            self._set_design_region(x_current, self.simulation, self.design_region)
            self._set_source_amplitude()

        # set up bounds on epsilon ((1, eps_m), (1, eps_m), ... ) for each grid in design region
        eps_bounds = tuple([(1, self.eps_max) for _ in range(np.sum(self.design_region == 1))])

        # start eps off with the one currently within design region
        x0 = self._get_design_region(self.simulation.eps_r, self.design_region)

        # minimize
        (x, _, _) = fmin_l_bfgs_b(_objfn, x0, fprime=_grad, args=(), approx_grad=0,
                            bounds=eps_bounds, m=10, factr=100,
                            pgtol=1e-08, epsilon=1e-08, iprint=-1,
                            maxfun=15000, maxiter=self.Nsteps, disp=self.verbose,
                            callback=_update_iter_count, maxls=20)

        # finally, set the simulation permittivity to that found via optimization
        self._set_design_region(x, self.simulation, self.design_region)

    def _set_source_amplitude(self, epsilon=1e-2, N=1):
        """ If max_index_shift specified, sets the self.simulation.src amplitude
            low enough so that this is satisfied.
            'epsilon' is the amount to subtract from source to get it under.
        """

        # keep a running list of the source amplitudes
        self.src_amplitudes.append(np.max(np.abs(self.simulation.src)))

        # if a max index shift is specified
        if self.max_ind_shift is not None:

            # for a number of iterations
            for _ in range(N):

                # compute the index shift and update the source according to the ratio
                dn = self.simulation.compute_index_shift()
                max_dn = np.max(dn)
                ratio = self.max_ind_shift / max_dn

                self.simulation.src = self.simulation.src * (np.sqrt(ratio) - epsilon)

    def _update_permittivity(self, gradient, step_size):
        """ Manually updates the permittivity with the gradient info """

        # deep copy original permittivity (deep for safety)
        eps_old = copy.deepcopy(self.simulation.eps_r)

        # update the old eps to get a new eps with the gradient
        eps_new = eps_old + self.design_region * step_size * gradient

        # push back inside bounds
        eps_new[eps_new < 1] = 1
        eps_new[eps_new > self.eps_max] = self.eps_max

        # reset the epsilon of the simulation
        self.simulation.eps_r = eps_new

        return eps_new

    def _step_adam(self, gradient, mopt_old, vopt_old, iteration, beta1, beta2, epsilon=1e-8):
        """ Performs one step of adam optimization"""

        mopt = beta1 * mopt_old + (1 - beta1) * gradient
        mopt_t = mopt / (1 - beta1**(iteration + 1))
        vopt = beta2 * vopt_old + (1 - beta2) * (np.square(gradient))
        vopt_t = vopt / (1 - beta2**(iteration + 1))
        grad_adam = mopt_t / (np.sqrt(vopt_t) + epsilon)

        return (grad_adam, mopt, vopt)

    def check_deriv(self, simulation, design_region, Npts=5, d_eps=1e-3):
        """ Returns a list of analytical and numerical derivatives to check gradient accuracy"""

        # make copy of original epsilon
        eps_orig = copy.deepcopy(simulation.eps_r)

        # solve for the linear fields and gradient of the linear objective function
        grad_avm = self.compute_dJ(simulation, design_region)
        J_orig = self.compute_J(simulation)

        avm_grads = []
        num_grads = []

        # for a number of points
        for _ in range(Npts):

            # pick a random point within the design region
            x, y = np.where(design_region == 1)
            i = np.random.randint(len(x))
            pt = [x[i], y[i]]

            # create a new, perturbed permittivity
            eps_new = copy.deepcopy(simulation.eps_r)
            eps_new[pt[0], pt[1]] += d_eps

            # make a copy of the current simulation
            sim_new = copy.deepcopy(simulation)
            sim_new.eps_r = eps_new

            # solve for the fields with this new permittivity
            J_new = self.compute_J(sim_new)

            # compute the numerical gradient
            grad_num = (J_new - J_orig)/d_eps

            # append both gradients to lists
            avm_grads.append(grad_avm[pt[0], pt[1]])
            num_grads.append(grad_num)

        return avm_grads, num_grads

    def plt_objs(self, norm=None, ax=None):
        """ Plots objective function vs. iteration"""

        iter_range = range(1, len(self.objfn_list) + 1)
        if norm == 'field':
            obj_scaled = [o/a for o, a in zip(self.objfn_list, self.src_amplitudes)]
            ax.set_ylabel('objective function / field')
        elif norm == 'power':
            obj_scaled = [o/a**2 for o, a in zip(self.objfn_list, self.src_amplitudes)]
            ax.set_ylabel('objective function / power')
        else:
            obj_scaled = self.objfn_list
            ax.set_ylabel('objective function')


        ax.plot(iter_range,  obj_scaled)
        ax.set_xlabel('iteration number')
        ax.set_title('optimization results')
        return ax

    def scan_frequency(self, Nf=50, df=1/20):
        """ Scans the objective function vs. frequency """

        # create frequencies (in Hz)
        delta_f = self.simulation.omega*df
        freqs = 1/2/np.pi*np.linspace(self.simulation.omega - delta_f/2,
                                      self.simulation.omega + delta_f/2,  Nf)

        bar = progressbar.ProgressBar(max_value=Nf)

        # loop through frequencies
        objs = []
        for i, f in enumerate(freqs):

            bar.update(i + 1)

            # make a new simulation object
            sim_new = copy.deepcopy(self.simulation)

            # reset the simulation to compute new A (hacky way of doing it)
            sim_new.omega = 2*np.pi*f
            sim_new.eps_r = self.simulation.eps_r

            # # compute the fields
            # (_, _, Ez) = sim_new.solve_fields()
            # (_, _, Ez_nl, _) = sim_new.solve_fields_nl()

            # compute objective function and append to list
            obj_fn = self.compute_J(sim_new)
            objs.append(obj_fn)

        # compute HM
        objs_array = np.array(objs)
        HM = np.max(objs_array)/2
        above_HM = objs_array > HM

        # does a scan up and down from the midpoint and counts number above HM in this peak
        num_above_HM = 0
        for i in range(int(Nf/2), Nf):
            if not above_HM[i]:
                break
            num_above_HM += 1
        for i in range(int(Nf/2)-1, -1, -1):
            if not above_HM[i]:
                break
            num_above_HM += 1

        # compute FWHM (Hz) using the number above HM and the freq difference
        FWHM = num_above_HM*(freqs[1] - freqs[0])

        return freqs, objs, FWHM

    def scan_power(self, probes=None, Ns=50, s_max=10):
        """ Scans the source amplitude and computes the objective function
            probes is a list of functions for computing the power, for example:
            [lambda simulation: simulation.flux_probe('x', [-NPML[0]-int(l/2/dl), ny + int(d/2/dl)], int(H/2/dl))]
        """

        if probes is None:
            raise ValueError("need to specify 'probes' kwarg as a list of functions for computing the power in each port.")
        num_probes = len(probes)

        # create src_amplitudes
        s_list = np.logspace(-6, np.log10(s_max), Ns)        

        bar = progressbar.ProgressBar(max_value=Ns)

        # transmission
        transmissions = [[] for _ in num_probes]

        for i, s in enumerate(s_list):

            bar.update(i + 1)

            # make a new simulation object
            sim_new = copy.deepcopy(self.simulation)

            # reset the simulation to compute new A (hacky way of doing it)
            sim_new.src = s * sim_new.src / np.max(np.abs(sim_new.src))

            # compute the fields
            _ = sim_new.solve_fields_nl()

            # compute power transmission using each probe
            for probe_index, probe in probes:
                W_out = probe(sim_new)
                sim_new.modes[0].compute_normalization(sim_new)                
                transmissions[probe_index][i] = W_out / W_in
        return transmissions

    def plot_transmissions(self, transmissions, legend=None):
        """ Plots the results of the power scan """

        for p in transmissions:
            plt.plot(p)
        plt.xscale('log')
        plt.xlabel('input source amplitude')
        plt.ylabel('power transmission')
        if legend is not None:
            plt.legend(legend)
        plt.show()

