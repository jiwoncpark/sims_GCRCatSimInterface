"""
This script will define classes that enable CatSim to interface with GCR
"""
__all__ = ["DESCQAObject", "bulgeDESCQAObject", "diskDESCQAObject", "knotsDESCQAObject",
           "deg2rad_double", "arcsec2rad", "SNFileDBObject"]

import numpy as np
import healpy
from lsst.sims.catalogs.db import fileDBObject

_GCR_IS_AVAILABLE = True
try:
    from GCR import GCRQuery
    from GCR import dict_to_numpy_array
    import GCRCatalogs
except ImportError:
    _GCR_IS_AVAILABLE = False

_LSST_IS_AVAILABLE = True
try:
    from lsst.sims.utils import _angularSeparation
except ImportError:
    _LSST_IS_AVAILABLE = False
    from astropy.coordinates import SkyCoord
    def _angularSeparation(ra1, dec1, ra2, dec2):
        return SkyCoord(ra1, dec1, unit="radian").separation(SkyCoord(ra2, dec2, unit="radian")).radian

def deg2rad_double(x):
    return np.deg2rad(x).astype(np.float64)

def arcsec2rad(x):
    return np.deg2rad(x/3600.0)


# a cache to store loaded catalogs to prevent them
# from being loaded more than once, eating up
# memory; this could happen since, for instance
# the same catalog will need to be queried twice
# go get bulges and disks from the same galaxy
_CATALOG_CACHE = {}
_ADDITIONAL_POSTFIX_CACHE = {}


class DESCQAChunkIterator(object):
    """
    This class mimics the ChunkIterator defined and used
    by CatSim.  It accepts a query to the catalog reader
    and allows CatSim to iterate over it one chunk at a
    time.
    """
    def __init__(self, descqa_obj, column_map, obs_metadata,
                 colnames, default_values=None, chunk_size=None):
        """
        Parameters
        ----------
        descqa_obj is the DESCQAObject querying the catalog

        column_map is the columnMap defined in DESCQAObject
        which controls the mapping between DESCQA columns
        and CatSim columns

        obs_metadata is an ObservationMetaData (a CatSim class)
        defining the telescope orientation at the time of the
        simulated observation

        colnames lists the names of the quantities that need
        to be queried from descqa_obj. These will consist of
        column names that can be queried directly by passing
        them to descqa_obj.get_quantities() as well as column
        names that can be mapped using the DESCQAObject.columns
        mapping and columns defined the
        DESCQAObject.dbDefaultValues

        default_values is a dict of default values to be used
        in the event that a quantity is missing from the
        catalog.

        chunk_size is an integer (or None) defining the number
        of rows to be returned at a time.
        """
        self._descqa_obj = descqa_obj
        self._column_map = column_map
        self._obs_metadata = obs_metadata
        self._colnames = colnames
        self._default_values = default_values
        self._chunk_size = int(chunk_size) if chunk_size else None
        self._native_filters = None
        self._data_indices = None
        self._loaded_qties = None

    def __iter__(self):
        return self

    def __next__(self):

        descqa_catalog = self._descqa_obj._catalog

        if self._data_indices is None:
            if self._loaded_qties is not None:
                raise RuntimeError("data_indices is None, but loaded_qties isn't "
                                   "in DESCQAChunkIterator")
            self._init_data_indices()
            qty_name_list = [self._column_map[name][0]
                             for name in self._colnames
                             if descqa_catalog.has_quantity(self._column_map[name][0])]

            self._loaded_qties = {}
            for name in qty_name_list:
                raw_qties = descqa_catalog.get_quantities(name,
                                                          native_filters=self._native_filters)
                self._loaded_qties[name] = raw_qties[name][self._data_indices]

            # since we are only keeping the objects that will ultimately go into
            # the catalog, we now change self._data_indices to range from 0
            # to the length of the final catalog; the indices relative to the
            # extragalactic catalog have been forgotten
            self._data_indices = np.arange(0, len(self._data_indices), dtype=int)

        data_indices_this = self._data_indices[:self._chunk_size]

        if not data_indices_this.size:
            self._loaded_qties = None
            self._data_indices = None
            raise StopIteration

        self._data_indices = self._data_indices[self._chunk_size:]

        # temporarily suppress divide by zero warnings
        with np.errstate(divide='ignore', invalid='ignore'):
            chunk = dict_to_numpy_array({name: self._loaded_qties[self._column_map[name][0]][data_indices_this]
                                         for name in self._colnames
                                         if descqa_catalog.has_quantity(self._column_map[name][0])})

        need_to_append_defaults = False
        for name in self._colnames:
            if not descqa_catalog.has_quantity(self._column_map[name][0]):
                need_to_append_defaults = True
                break

        if need_to_append_defaults:

            dtype_list = [(name, chunk.dtype[name]) for name in chunk.dtype.names]
            for name in self._colnames:
                if not descqa_catalog.has_quantity(self._column_map[name][0]):
                    dtype_tuple = (name, self._default_values[name][1])
                    if dtype_tuple not in dtype_list:
                        dtype_list.append(dtype_tuple)

            new_dtype = np.dtype(dtype_list)

            new_chunk = np.zeros(len(chunk), dtype=new_dtype)
            for name in self._colnames:
                if name in chunk.dtype.names:
                    new_chunk[name] = chunk[name]
                else:
                    new_chunk[name] = self._default_values[name][0]

            chunk = new_chunk

        return self._descqa_obj._postprocess_results(chunk)

    next = __next__

    def _init_data_indices(self):
        """
        Do the spatial filtering of extragalactic catalog data.
        """

        self._native_filters = None
        descqa_catalog = self._descqa_obj._catalog

        if self._obs_metadata is None or self._obs_metadata._boundLength is None:
            self._data_indices = np.arange(self._descqa_obj._catalog['raJ2000'].size)

        else:
            try:
                radius_rad = max(self._obs_metadata._boundLength[0],
                                 self._obs_metadata._boundLength[1])
            except (TypeError, IndexError):
                radius_rad = self._obs_metadata._boundLength

            if 'healpix_pixel' in descqa_catalog._native_filter_quantities:
                ra_rad = self._obs_metadata._pointingRA
                dec_rad = self._obs_metadata._pointingDec
                vv = np.array([np.cos(dec_rad)*np.cos(ra_rad),
                               np.cos(dec_rad)*np.sin(ra_rad),
                               np.sin(dec_rad)])
                healpix_list = healpy.query_disc(8, vv, radius_rad,
                                                 inclusive=True,
                                                 nest=False)

                healpix_filter = None
                for hh in healpix_list:
                    local_filter = GCRQuery('healpix_pixel==%d' % hh)
                    if healpix_filter is None:
                        healpix_filter = local_filter
                    else:
                        healpix_filter |= local_filter

                if healpix_filter is not None:
                    if self._native_filters is None:
                        self._native_filters = [healpix_filter]
                    else:
                        self._native_filters.append(healpix_filter)

            ra_dec = descqa_catalog.get_quantities(['raJ2000', 'decJ2000'],
                                                   native_filters=self._native_filters)

            ra = ra_dec['raJ2000']
            dec = ra_dec['decJ2000']

            self._data_indices = np.where(_angularSeparation(ra, dec, \
                    self._obs_metadata._pointingRA, \
                    self._obs_metadata._pointingDec) < radius_rad)[0]

        if self._chunk_size is None:
            self._chunk_size = self._data_indices.size


class DESCQAObject(object):
    """
    This class is meant to mimic the CatalogDBObject usually used to
    connect CatSim to a database.
    """

    objectTypeId = None
    verbose = False
    database = 'LSSTCATSIM'

    epoch = 2000.0
    idColKey = 'galaxy_id'

    # The descqaDefaultValues set the values of columns that
    # are needed but are not in the underlying catalog.
    # The keys are the names of the columns.  The values are
    # tuples.  The first element of the tuple is the actual
    # default value. The second element of the tuple is
    # the dtype of the value (i.e. the argument that gets
    # passed to np.dtype())
    descqaDefaultValues = {'is_sprinkled': (0, int),
                           'internalRv_dc2': (np.NaN, float),
                           'internalAv_dc2': (np.NaN, float),
                           'sedFilename_dc2': (None, (str, 200)),
                           'magNorm_dc2': (np.NaN, float),
                           'varParamStr': (None, (str, 500))}

    _columns_need_postfix = ('majorAxis', 'minorAxis', 'sindex')
    _postfix = None
    _cat_cache_suffix = '_standard'  # so that different DESCQAObject
                                     # classes with different
                                     # self._transform_catalog()
                                     # methods can be loaded simultaneously

    def __init__(self, yaml_file_name=None, config_overwrite=None):
        """
        Parameters
        ----------
        yaml_file_name is the name of the yaml file that will tell DESCQA
        how to load the catalog
        """

        if yaml_file_name is None:
            if not hasattr(self, 'yaml_file_name'):
                raise RuntimeError('No yaml_file_name specified for '
                                   'DESCQAObject')

            yaml_file_name = self.yaml_file_name

        if not _GCR_IS_AVAILABLE:
            raise RuntimeError("You cannot use DESCQAObject\n"
                               "You do not have *GCR* installed and setup")

        if yaml_file_name + self._cat_cache_suffix not in _CATALOG_CACHE:
            gc = GCRCatalogs.load_catalog(yaml_file_name, config_overwrite)
            additional_postfix = self._transform_catalog(gc)
            _CATALOG_CACHE[yaml_file_name + self._cat_cache_suffix] = gc
            _ADDITIONAL_POSTFIX_CACHE[yaml_file_name + self._cat_cache_suffix] = \
                                      additional_postfix

        self._catalog = _CATALOG_CACHE[yaml_file_name + self._cat_cache_suffix]

        if self._columns_need_postfix:
            self._columns_need_postfix += _ADDITIONAL_POSTFIX_CACHE[yaml_file_name + self._cat_cache_suffix]
        else:
            self._columns_need_postfix = _ADDITIONAL_POSTFIX_CACHE[yaml_file_name + self._cat_cache_suffix]

        self._catalog_id = yaml_file_name + self._cat_cache_suffix
        self._make_column_map()
        self._make_default_values()

        if self.objectTypeId is None:
            raise RuntimeError("Need to define objectTypeId for your DESCQAObject")

        if self.idColKey is None:
            raise RuntimeError("Need to define idColKey for your DESCQAObject")

    def _transform_object_coords(self, gc):
        """
        Apply transformations to the RA, Dec of astrophysical sources;

        gc is a GCR catalog instance
        """
        gc.add_derived_quantity('raJ2000', deg2rad_double, 'ra_true')
        gc.add_derived_quantity('decJ2000', deg2rad_double, 'dec_true')

    def _transform_catalog(self, gc):
        """
        Accept a GCR catalog object and add transformations to the
        columns in order to get the quantities expected by the CatSim
        code.
        In case these quantities require additional postfix filters, as is the
        case for the GCR knots add-on, this function returns the column names

        Parameters
        ----------
        gc -- a GCRCatalog object;
              the result of calling GCRCatalogs.load_catalog()

        Returns
        -------
        additional_postfix -- tuple of string;
            Additional column names, if any, to process through the postfix
            filter, besides the default fields already specified in _columns_need_postfix.
        """
        self._transform_object_coords(gc)

        gc.add_quantity_modifier('redshift', gc.get_quantity_modifier('redshift_true'), overwrite=True)
        gc.add_quantity_modifier('true_redshift', gc.get_quantity_modifier('redshift_true'))
        gc.add_quantity_modifier('gamma1', gc.get_quantity_modifier('shear_1'))
        gc.add_quantity_modifier('gamma2', gc.get_quantity_modifier('shear_2_phosim'))
        gc.add_quantity_modifier('kappa', gc.get_quantity_modifier('convergence'))

        gc.add_derived_quantity('positionAngle', np.radians, 'position_angle_true')

        gc.add_derived_quantity('majorAxis::disk', arcsec2rad, 'size_disk_true')
        gc.add_derived_quantity('minorAxis::disk', arcsec2rad, 'size_minor_disk_true')
        gc.add_derived_quantity('majorAxis::bulge', arcsec2rad, 'size_bulge_true')
        gc.add_derived_quantity('minorAxis::bulge', arcsec2rad, 'size_minor_bulge_true')

        gc.add_quantity_modifier('sindex::disk', gc.get_quantity_modifier('sersic_disk'))
        gc.add_quantity_modifier('sindex::bulge', gc.get_quantity_modifier('sersic_bulge'))

        additional_postfix = ()

        # Test for random walk specific addon
        # This gets activated only if the catalog is a composite, and only if one
        # of the composite catalogs is a knots catalog
        cat_info = gc.get_catalog_info()
        if cat_info['subclass_name'] == 'composite.CompositeReader':
            if 'knots' in [c['catalog_name'] for c in cat_info['catalogs']]:
                additional_postfix += self._transform_knots(gc)

        return additional_postfix

    def _transform_knots(self, gc):
        """
        Accepts a GCR catalog object and add transformations to the
        columns in order to get the parameters for the knots component.

        Parameters
        ----------
        gc -- a GCRCatalog object;
              the result of calling GCRCatalogs.load_catalog()

        Returns
        -------
        additional_postfix -- list of string;
            Additional column names, if any, to process through the postfix filter.
        """
        # Hacky solution, the number of knots replaces the sersic index,
        # keeping the rest of the sersic parameters, which are directly applicable
        gc.add_derived_quantity('sindex::knots', lambda x:x, 'n_knots')
        gc.add_derived_quantity('majorAxis::knots', arcsec2rad, 'size_disk_true')
        gc.add_derived_quantity('minorAxis::knots', arcsec2rad, 'size_minor_disk_true')

        # Apply flux correction for the random walk
        add_postfix = []
        for name in gc.list_all_native_quantities():
            if 'SEDs/diskLuminositiesStellar:SED' in name:
                # The epsilon value is to keep the disk component, so that
                # the random sequence in extinction parameters is preserved
                eps = np.finfo(np.float32).eps
                gc.add_derived_quantity(name+'::disk', lambda x,y: x*np.clip(1-y, eps, None), name, 'knots_flux_ratio')
                gc.add_derived_quantity(name+'::knots', lambda x,y: x*np.clip(y, eps,None), name, 'knots_flux_ratio')
                add_postfix.append(name)

        # Returning these columns so that they can be registered for postfix filtering
        return tuple(add_postfix)

    def getIdColKey(self):
        return self.idColKey

    def getObjectTypeId(self):
        return self.objectTypeId

    def _make_default_values(self):
        """
        Create the self._descqaDefaultValues member that will
        ultimately be passed to the DESCQAChunkIterator
        """
        self._descqaDefaultValues = self.descqaDefaultValues

    def _make_column_map(self):
        """
        Slightly different from the database case.
        self.columnMap will be a dict keyed on the CatSim column name.
        The values will be tuples.  The first element of the tuple is the
        GCR column name corresponding to that CatSim column.  The second
        element is an (optional) transformation applied to the GCR column
        used to get it into units expected by CatSim.
        """
        self.columnMap = dict()
        self.columns = []

        for name in self._catalog.list_all_quantities(include_native=True):
            self.columnMap[name] = (name,)
            self.columns.append((name, name))

        if self._columns_need_postfix:
            if not self._postfix:
                raise ValueError('must specify `_postfix` when `_columns_need_postfix` is not empty')
            for name in self._columns_need_postfix:
                self.columnMap[name] = (name + self._postfix,)
                self.columns.append((name, name+self._postfix))

        if hasattr(self, 'descqaDefaultValues'):
            for col_name in self.descqaDefaultValues:
                self.columnMap[col_name] = (col_name,)

    def _postprocess_results(self, chunk):
        """
        A method to add optional data before passing the results
        to the InstanceCatalog class

        This is included to preserve similarity to the API of
        lsst.sims.catalogs.db.CatalogDBObject
        """
        return self._final_pass(chunk)

    def _final_pass(self, chunk):
        """
        Last chance to inject data into the query results before
        passing to the InstanceCatalog class

        This is included to preserve similiarity to the API of
        lsst.sims.catalogs.db.CatalogDBObject
        """
        return chunk

    def query_columns(self, colnames=None, chunk_size=None,
                      obs_metadata=None, constraint=None, limit=None):
        """
        Parameters
        ----------
        colnames is a list of column names to be queried (CatSim
        will determine which automaticall)

        chunk_size is the number of rows to return at a time

        obs_metadata is an ObservationMetaData defining the orientation
        of the telescope

        constraint is ignored, but needs to be here to preserve the API

        limit is ignored, but needs to be here to preserve the API
        """
        return DESCQAChunkIterator(self, self.columnMap, obs_metadata,
                                   colnames or list(self.columnMap),
                                   self._descqaDefaultValues,
                                   chunk_size)


class bulgeDESCQAObject(DESCQAObject):
    # PhoSim uniqueIds are generated by taking
    # source catalog uniqueIds, multiplying by
    # 1024, and adding objectTypeId.  This
    # components of the same galaxy to have
    # different uniqueIds, even though they
    # share a uniqueId in the source catalog
    objectTypeId = 77

    # some column names require an additional postfix
    _postfix = '::bulge'


class diskDESCQAObject(DESCQAObject):
    objectTypeId = 87
    _postfix = '::disk'


class knotsDESCQAObject(DESCQAObject):
    objectTypeId = 95
    _postfix = '::knots'


class SNFileDBObject(fileDBObject):
    """
    Use FileDBObject to provide CatalogDBObject functionality for SN
    with host galaxies from protoDC2 output to csv files before
    """
    dbDefaultValues = {'varsimobjid':-1,
                       'runid':-1,
                       'ismultiple':-1,
                       'run':-1,
                       'runobjid':-1}

    # These types should be matched to the database.
    #: Default map is float.  If the column mapping is the same as the
    # column name, None can be specified

    columns = [('raJ2000', 'snra_in*PI()/180.'),
               ('decJ2000', 'sndec_in*PI()/180.'),
               ('Tt0', 't0_in'),
               ('Tx0', 'x0_in'),
               ('Tx1', 'x1_in'),
               ('Tc', 'c_in'),
               ('id', 'snid_in'),
               ('Tredshift', 'z_in'),
               ('redshift', 'z_in'),
              ]
