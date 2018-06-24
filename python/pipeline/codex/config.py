import json
import os
import codex
from codex import tiling
from os import path as osp
from collections import namedtuple


TILING_MODE_SNAKE = 'snake'


def _load_json_config(data_dir, filename, default=None):
    f = osp.join(data_dir, filename)
    if not osp.exists(f):
        if default is None:
            raise ValueError('Required configuration file "{}" does not exist'.format(f))
        else:
            return default
    with open(f, 'r') as fd:
        return json.load(fd)


def _load_experiment_config(data_dir):
    return _load_json_config(data_dir, 'Experiment.json')


def _load_processing_options(data_dir):
    return _load_json_config(data_dir, 'processingOptions.json', default={})


def _load_channel_names(data_dir):
    f = osp.join(data_dir, 'channelNames.txt')
    if not osp.exists(f):
        raise ValueError('Required channel names configuration file "{}" does not exist'.format(f))
    with open(f, 'r') as fd:
        return [l.strip() for l in fd.readlines() if l.strip()]


TileDims = namedtuple('TileDims', ['cycles', 'z', 'channels', 'height', 'width'])
TileIndices = namedtuple('TileIndices', ['region_index', 'tile_index', 'tile_x', 'tile_y'])


class Config(object):

    @property
    def n_tiles_per_region(self):
        return self.region_width * self.region_height

    @property
    def tile_dims(self):
        """Get tile dimensions as (cycles, z, channels, height, width)"""
        return TileDims(self.n_cycles, self.n_z_planes, self.n_channels_per_cycle, self.tile_height, self.tile_width)

    def get_tile_indices(self):
        """Get coordinates (as indexes) of experiment tiles

        Returns:
            List of TileIndices tuples like (region_index, tile_index, tile_x, tile_y)
        """
        indices = []
        for ireg in self.region_indexes:
            for itile in range(self.n_tiles_per_region):
                tx, ty = self.get_tile_coordinates(itile)
                indices.append(TileIndices(ireg, itile, tx, ty))
        return indices

    def get_tile_coordinates(self, tile_index):
        """Get 0-based X and Y coordinates of a tile using the configured 'tiling_mode'

        Args:
            tile_index: 0-based tile index
        Returns:
            Integer tuple as (X, Y) where 0 <= X < region_width and 0 <= Y < region_height
        """
        if tile_index >= self.n_tiles_per_region:
            raise ValueError(
                'Cannot get coordinates for tile with 0-based index {} when only {} '
                'iles are expected (region width = {}, height = {})'
                    .format(tile_index, self.n_tiles_per_region, self.region_width, self.region_height)
            )
        tiler = tiling.get_tiling_by_name(self.tiling_mode)
        return tiler.coordinates_from_index(tile_index, w=self.region_width, h=self.region_height)

    def get_channel_coordinates(self, channel_name):
        """Get 0-based cycle and per-cycle-channel index coordinates for a channel name

        Args:
            channel_name: String name of channel
        Returns:
            (cycle, channel) - 0-based indexes for cycle and channel
        """
        if channel_name not in self.channel_names:
            raise ValueError('Channel "{}" is not configured channel list {}'.format(channel_name, self.channel_names))
        i = self.channel_names.index(channel_name)
        cycle_index = i // self.n_cycles
        ch_index = i % self.n_cycles
        return cycle_index, ch_index


class CodexConfigV1(Config):

    def __init__(self, conf):
        self._conf = conf

    @property
    def channel_names(self):
        return self._conf['all_channel_names']

    @property
    def n_cycles(self):
        return self._conf['num_cycles']

    @property
    def n_z_planes(self):
        return self._conf['num_z_planes']

    @property
    def n_channels_per_cycle(self):
        return len(self._conf['channel_names'])

    @property
    def tile_width(self):
        return self._conf['tile_width']

    @property
    def tile_height(self):
        return self._conf['tile_height']

    @property
    def overlap_x(self):
        return self._conf['tile_overlap_X']

    @property
    def overlap_y(self):
        return self._conf['tile_overlap_Y']

    @property
    def region_width(self):
        return self._conf['region_width']

    @property
    def region_height(self):
        return self._conf['region_height']

    @property
    def tiling_mode(self):
        return self._conf['tiling_mode']

    @property
    def region_indexes(self):
        """Get 0-based region index list"""
        return [i - 1 for i in self._conf['regIdx']]

    @property
    def drift_compensation_reference(self):
        """Get reference image configured for drift compensation
        Returns:
            (cycle, channel) - 0-based indexes for cycle and channel
        """
        cycle = self._conf['driftCompReferenceCycle'] - 1
        channel = self._conf['drift_comp_channel'] - 1
        return cycle, channel

    @property
    def best_focus_reference(self):
        """Get reference image configured for best focus plan selection
        Returns:
            (cycle, channel) - 0-based indexes for cycle and channel
        """
        cycle = self._conf['bestFocusReferenceCycle'] - 1
        channel = self._conf['best_focus_channel'] - 1
        return cycle, channel

    @property
    def cytometry_reference(self):
        nuc_name = self._conf['cytometry_nuclei_channel_name']
        mem_name = self._conf.get('cytometry_membrane_channel_name')
        params = self._conf.get('cytometry_params')
        return self.get_channel_coordinates(nuc_name), \
               self.get_channel_coordinates(mem_name) if mem_name else None, \
               params


    @property
    def _n_actual_channels(self):
        return len(self.channel_names)

    @property
    def _n_expected_channels(self):
        return self.n_cycles * self.n_channels_per_cycle

    @property
    def microscope_params(self):
        mag = self._conf['magnification']
        na = self._conf['numerical_aperture']
        res_axial_nm = self._conf['z_pitch']
        res_lateral_nm = self._conf['per_pixel_XY_resolution']
        objective_type = self._conf['objectiveType']
        em_wavelength_nm = self._conf['emission_wavelengths']
        return mag, na, res_axial_nm, res_lateral_nm, objective_type, em_wavelength_nm

    def _validate(self):
        # Ensure that number of channel names equals expected number
        if self._n_actual_channels != self._n_expected_channels:
            raise ValueError(
                'Full list of channel names does not have length equal '
                'to num_cycles * n_channels_per_cycle; '
                'n expected channel names = {}, n actual channel names = {}'
                .format(self._n_expected_channels, self._n_actual_channels)
            )

        # Ensure that all one-based indexes do not have a value <= 0
        def validate_one_based_index(k, v=None):
            if v is None:
                v = self._conf[k]
            if v <= 0 or not isinstance(v, int):
                raise ValueError('Expected 1-based index for "{}" to be int > 0 but found value {}'.format(k, v))
        for k in [
            'driftCompReferenceCycle', 'drift_comp_channel',
            'bestFocusReferenceCycle', 'best_focus_channel',
        ]:
            validate_one_based_index(k)
        for ireg in self._conf['regIdx']:
            validate_one_based_index('regIdx', v=ireg)
        return self

    @staticmethod
    def load(data_dir, overrides=None):
        """Load all CODEX related configuration files given a primary data directory"""
        conf = _load_experiment_config(data_dir)
        conf.update(_load_processing_options(data_dir))
        conf.update(dict(all_channel_names=_load_channel_names(data_dir)))
        if overrides:
            conf.update(overrides)
        return CodexConfigV1(conf)._validate()


def load(data_dir):
    version = codex.get_config_version()
    if version == codex.CONFIG_V01:
        return CodexConfigV1.load(data_dir)
    else:
        raise ValueError(
            'CODEX Version "{}" not supported (determined by env variable {})'
            .format(version, codex.ENV_CONFIG_VERSION)
        )


def load_example_config(example_name):
    """Load example configuration stored at codex/config/$SCHEMA_VERSION/examples"""
    return load(osp.join(codex.conf_dir, codex.get_config_version(), 'examples', example_name))
