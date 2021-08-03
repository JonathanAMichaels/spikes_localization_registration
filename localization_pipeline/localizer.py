import os
import numpy as np
from tqdm import tqdm
from denoiser import Denoise
import scipy.optimize as optim_ls

class LOCALIZER(object):
    
    def __init__(self, bin_file, residual_file, dtype, spike_train_path, templates_path, geom_path, denoiser_weights, denoiser_min,
                 n_filters = [16, 8, 4], filter_sizes = [5, 11, 21], sampling_rate = 30000,
                 multi_processing = 1, n_processors = 5, spike_size = 121, n_channels_loc = 10):
        self.bin_file = bin_file
        self.residual_file = bin_file
        self.dtype = np.dtype(dtype)
        self.spike_train = np.load(spike_train_path)
        self.spike_train = self.spike_train[self.spike_train[:, 0].argsort()]
        if templates_path is not None:
            self.templates = np.load(templates_path)
        else:
            self.get_templates()
        self.multi_processing = multi_processing
        self.n_processors = n_processors
        self.spike_size = spike_size
        self.geom_array = np.load(geom_path)
        self.sampling_rate = sampling_rate
        self.n_channels = self.geom_array.shape[0]
        self.denoiser_weights = denoiser_weights
        self.denoiser_min = denoiser_min
        self.n_filters = n_filters,
        self.filter_sizes = filter_sizes,
        self.n_channels_loc = n_channels_loc # NUmber of channels used for localizing -> Change for spatial radius / depend on geometry array
        
    def read_waveforms(self, spike_times, n_times=None, channels=None):
        '''
        read waveforms from recording
        n_times : waveform temporal length 
        channels : channels to read from 
        '''

        if n_times is None:
            n_times = self.spike_size

        # n_times needs to be odd
        if n_times % 2 == 0:
            n_times += 1

        # read all channels
        if channels is None:
            channels = np.arange(self.geom_array.shape[0])

        # ***** LOAD RAW RECORDING *****
        wfs = np.zeros((len(spike_times), n_times, len(channels)),
                       'float32')

        skipped_idx = []
        total_size = n_times*self.n_channels
        # spike_times are the centers of waveforms
        spike_times_shifted = spike_times - n_times//2
        offsets = spike_times_shifted.astype('int64')*self.dtype.itemsize*self.n_channels
        with open(self.residual_file, "rb") as fin:
            for ctr, spike in enumerate(spike_times_shifted):
                try:
                    fin.seek(offsets[ctr], os.SEEK_SET)
                    wf = np.fromfile(fin,
                                     dtype=self.dtype,
                                     count=total_size)
                    wfs[ctr] = wf.reshape(
                        n_times, self.n_channels)[:,channels]
                except:
                    skipped_idx.append(ctr)
        wfs=np.delete(wfs, skipped_idx, axis=0)
        fin.close()

        return wfs, skipped_idx
    
    def load_denoiser(self):
        if torch.cuda.is_available():
            self.device = "cuda:0"
        else:
            self.device = "cpu"
#         torch.cuda.set_device(CONFIG.resources.gpu_id)

        self.denoiser = Denoise(self.n_filters[0],
                   self.filter_sizes[0],
                   self.spike_size)
        self.denoiser.load(self.denoiser_weights)
        self.denoiser = self.denoiser.cuda()
    
    def denoise_wf_nn_tmp(self, wf):
        denoiser = self.denoiser.to(self.device)
        n_data, n_times, n_chans = wf.shape
        if wf.shape[0]>0:
            wf_reshaped = wf.transpose(0, 2, 1).reshape(-1, n_times)
            wf_torch = torch.FloatTensor(wf_reshaped).to(self.device)
            denoised_wf = denoiser(wf_torch)[0].data
            denoised_wf = denoised_wf.reshape(
                n_data, n_chans, n_times)
            denoised_wf = denoised_wf.cpu().data.numpy().transpose(0, 2, 1)

            # reshape it
            #window = np.arange(15, 40)
            #denoised_wf = denoised_wf[:, window]
    #         denoised_wf = denoised_wf.reshape(n_data, -1)
            del wf_torch
        else:
            denoised_wf = np.zeros((wf.shape[0], wf.shape[1]*wf.shape[2]),'float32')

        return denoised_wf


        
    def subsample(self, num_obs, events, units):
        events_sampled = -10*np.ones(events.shape)
        units_sampled = -10*np.ones(units.shape)
        for unit in np.unique(units):
            spike_times_unit = np.where(units == unit)[0]
            idx = np.random.choice(np.arange(0, spike_times_unit.shape[0]), size=min(num_obs, spike_times_unit.shape[0]), replace=False)
            spike_times_unit = spike_times_unit[idx]
            events_sampled[spike_times_unit] = events[spike_times_unit].copy()
            units_sampled[spike_times_unit] = units[spike_times_unit].copy()
        for unit in [-1, -2]:
            spike_times_unit = np.where(units == unit)[0]
            idx = np.random.choice(np.arange(0, spike_times_unit.shape[0]), size=min(num_obs, spike_times_unit.shape[0]), replace=False)
            spike_times_unit = spike_times_unit[idx]
            events_sampled[spike_times_unit] = events[spike_times_unit].copy()
            units_sampled[spike_times_unit] = units[spike_times_unit].copy()
        return events_sampled[events_sampled >= -5], units_sampled[units_sampled >= -5]
        
    def get_templates(self):
        if self.templates is None:
            units = np.unique(self.spike_train[:, 1])
            self.templates = np.zeros((units.max()+1, 121, 384))
            for unit in tqdm(units):
                spike_times, spike_units = self.subsample(250, self.spike_train[self.spike_train[:, 1]==unit][:, 0], self.spike_train[self.spike_train[:, 1]==unit][:, 1])
                wfs = self.read_waveforms(spike_times)[0]
                mc = wfs.mean(0).ptp(0).argmax()
    #             wfs = shift_chans(wfs, align_get_shifts_with_ref(wfs[:,:,mc], nshifts = 25))
                if wfs.shape[0]>0:
                    self.templates[unit] = wfs.mean(0)

    def get_offsets(self):
        self.offsets = np.zeros(self.templates.shape[0])
        for unit in range(self.templates.shape[0]):
            self.offsets[unit] = self.templates[unit][:, self.templates[unit].ptp(0).argmax()].argmin() - self.denoiser_min

    def compute_aligned_templates(self):
        units = np.unique(self.spike_train[:, 1])
        self.templates_aligned = np.zeros((units.max()+1, 121, 384))
        self.get_offsets()
        for unit in tqdm(units):
            spike_times, spike_units = self.subsample(250, self.spike_train[self.spike_train[:, 1]==unit][:, 0], self.spike_train[self.spike_train[:, 1]==unit][:, 1])
            spike_times += int(self.offsets[unit])
            wfs = self.read_waveforms(spike_times)[0]
            mc = wfs.mean(0).ptp(0).argmax()
#             wfs = shift_chans(wfs, align_get_shifts_with_ref(wfs[:,:,mc], nshifts = 25))
            if wfs.shape[0]>0:
                self.templates_aligned[unit] = wfs.mean(0)

    def minimize_ls(self, vec, wfs_0, z_initial, channels):
        return wfs_0.ptp(1)-vec[3]/(((self.geom_array[channels] - [vec[0], z_initial+vec[1]])**2).sum(1) + vec[2]**2)**0.5 # vec[0]


    def get_estimate(self, batch_id, threshold = 6, output_directory = 'position_results_files'):
        
        spike_times_batch = self.spike_train[np.logical_and(self.spike_train[:, 0] >= batch_id*self.sampling_rate, self.spike_train[:, 0] < (batch_id+1)*self.sampling_rate), 0]
        spike_units_batch = self.spike_train[np.logical_and(self.spike_train[:, 0] >= batch_id*self.sampling_rate, self.spike_train[:, 0] < (batch_id+1)*self.sampling_rate), 1]

        time_width = np.zeros(spike_times_batch.shape[0])
        results_x = np.zeros(spike_times_batch.shape[0])
        results_x_mean = np.zeros(spike_times_batch.shape[0])
        results_alpha = np.zeros(spike_times_batch.shape[0])
        results_y = np.zeros(spike_times_batch.shape[0])
        results_spread = np.zeros(spike_times_batch.shape[0])
        results_max_ptp = np.zeros(spike_times_batch.shape[0])
        results_z = np.zeros(spike_times_batch.shape[0])
        max_channels = np.zeros(spike_times_batch.shape[0])
        results_z_mean = np.zeros(spike_times_batch.shape[0])
        results_times = np.zeros(spike_times_batch.shape[0])

        for i in (range(spike_times_batch.shape[0])):
            unit = spike_units_batch[i]
            channels = np.arange(0, self.n_channels)
            wfs_0, skipped_idx = self.read_waveforms(np.asarray([int(spike_times_batch[i] + self.offsets[unit])]))
            if len(skipped_idx) == 0:
                wfs_0 += self.templates_aligned[int(spike_units_batch[i])].reshape((1, 121, 384))
                wfs_0 = self.denoise_wf_nn_tmp(wfs_0)[0]
                mc = wfs_0.ptp(0).argmax()
                if wfs_0.ptp(0).max() > threshold:
                    time_width[i] = np.abs(wfs_0[:, mc].argmax() - wfs_0[:, mc].argmin())
                    max_channels[i] = channels[mc]
                    if mc <= self.n_channels_loc//2:
                        channels_wfs = np.arange(0, self.n_channels_loc)
                    elif mc >= self.n_channels - self.n_channels_loc//2:
                        channels_wfs = np.arange(self.n_channels - self.n_channels_loc, self.n_channels)
                    else:
                        channels_wfs = np.arange(mc - self.n_channels_loc//2, mc + self.n_channels_loc//2)
                    results_z_mean[i] = (wfs_0.ptp(0)[channels_wfs]*self.geom_array[channels[channels_wfs], 1]).sum()/wfs_0.ptp(0)[channels_wfs].sum()
                    x_init = (wfs_0.ptp(0)[channels_wfs]*self.geom_array[channels[channels_wfs], 0]).sum()/wfs_0.ptp(0)[channels_wfs].sum()
                    results_x_mean[i] = x_init

                    results_max_ptp[i] = wfs_0.ptp(0).max()

                    output = optim_ls.least_squares(self.minimize_ls, x0=[results_x_mean[i], 0, 21, 1000], bounds = ([-100, -100, 0, 0], [132, 100, 250, 10000]), args=(wfs_0[:, channels_wfs].T, results_z_mean[i], channels[channels_wfs]))['x']

                    results_x[i] = output[0]
                    results_z[i] = self.geom_array[channels[mc], 1] + output[1] 
                    results_alpha[i] = output[3]
                    results_y[i] = np.abs(output[2]) #max(25, (output[2]/wfs_0.ptp(0)[channels_wfs].max() - ((CONFIG.geom[channels[mc]] - [output[0] , CONFIG.geom[channels[mc], 1] + output[1]])**2).sum()).mean())
                    results_spread[i] = (wfs_0.ptp(0)[channels_wfs]*((self.geom_array[channels[channels_wfs]] - [results_x[i], results_z[i]])**2).sum(1)).sum()/wfs_0.ptp(0)[channels_wfs].sum()
                    results_times[i] = 1

        fname_time_width = os.path.join(output_directory, 'results_width_{}.npy'.format(str(batch_id).zfill(6)))
        fname_z = os.path.join(output_directory, 'results_z_{}.npy'.format(str(batch_id).zfill(6)))     
        fname_x = os.path.join(output_directory, 'results_x_{}.npy'.format(str(batch_id).zfill(6)))
        fname_z_mean = os.path.join(output_directory, 'results_z_mean_{}.npy'.format(str(batch_id).zfill(6)))     
        fname_x_mean = os.path.join(output_directory, 'results_x_mean_{}.npy'.format(str(batch_id).zfill(6)))
        fname_spread = os.path.join(output_directory, 'results_spread_{}.npy'.format(str(batch_id).zfill(6)))
        fname_max_ptp = os.path.join(output_directory, 'results_max_ptp_{}.npy'.format(str(batch_id).zfill(6)))
        fname_y = os.path.join(output_directory, 'results_y_{}.npy'.format(str(batch_id).zfill(6)))
        fname_alpha = os.path.join(output_directory, 'results_alpha_{}.npy'.format(str(batch_id).zfill(6)))
        fname_max_channels = os.path.join(output_directory, 'results_max_channels_{}.npy'.format(str(batch_id).zfill(6)))
        fname_times_read = os.path.join(output_directory, 'times_read_{}.npy'.format(str(batch_id).zfill(6)))
        
        np.save(fname_z, results_z)
        np.save(fname_x, results_x)
        np.save(fname_z_mean, results_z_mean)
        np.save(fname_x_mean, results_x_mean)
        np.save(fname_time_width, time_width)
        np.save(fname_max_channels, max_channels)
        np.save(fname_max_ptp, results_max_ptp)
        np.save(fname_spread, results_spread)
        np.save(fname_alpha, results_alpha)
        np.save(fname_y, results_y)
        np.save(fname_times_read, results_times)


