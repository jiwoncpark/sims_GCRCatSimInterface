"""
This script will define classes that enable CatSim to interface with GCR
"""
import numpy as np

from desc.sims.GCRCatSimInterface import DESCQAObject
from desc.sims.GCRCatSimInterface import deg2rad_double, arcsec2rad


__all__ = ["DESCQAObject_protoDC2",
           "bulgeDESCQAObject_protoDC2",
           "diskDESCQAObject_protoDC2"]


_LSST_IS_AVAILABLE = True
try:
    from lsst.sims.utils import rotationMatrixFromVectors
    from lsst.sims.utils import cartesianFromSpherical, sphericalFromCartesian
except ImportError:
    _LSST_IS_AVAILABLE = False


class DESCQAObject_protoDC2(DESCQAObject):
    """
    This class is meant to mimic the CatalogDBObject usually used to
    connect CatSim to a database.
    """

    _cat_cache_suffix = '_rotated'

    def _rotate_to_correct_field(self, ra_rad, dec_rad):
        """
        Takes arrays of RA and Dec (in radians) centered
        on RA=0, Dec=0 and rotates them to coordinates
        centered on self.field_ra, self.field_dec

        Returns the rotated RA, Dec in radians.
        """

        if not _LSST_IS_AVAILABLE:
            raise RuntimeError("\nCannot use DESCQAObject_protoDC2\n"
                               "The LSST simulations stack is not setup\n")

        if not hasattr(self, '_rotate_ra_in_cache'):
            self._rotate_ra_in_cache = None
            self._rotate_dec_in_cache = None

        if not hasattr(self, '_field_rot_matrix'):
            dc2_origin = cartesianFromSpherical(0.0, 0.0)
            correct_field = cartesianFromSpherical(np.radians(self.field_ra),
                                                   np.radians(self.field_dec))

            if np.abs(1.0-np.dot(dc2_origin, correct_field))<1.0e-7:
                self._field_rot_matrix = np.identity(3, dtype=float)
            else:
                self._field_rot_matrix = rotationMatrixFromVectors(dc2_origin,
                                                                   correct_field)


        if self._rotate_ra_in_cache is None or \
           not np.array_equal(ra_rad, self._rotate_ra_in_cache) or \
           not np.array_equal(dec_rad, self._rotate_dec_in_cache):

            xyz = cartesianFromSpherical(ra_rad, dec_rad).transpose()
            xyz_rotated = np.dot(self._field_rot_matrix, xyz).transpose()

            self._rotate_ra_in_cache = ra_rad
            self._rotate_dec_in_cache = dec_rad

            (self._ra_rotated,
             self._dec_rotated) = sphericalFromCartesian(xyz_rotated)

        return self._ra_rotated, self._dec_rotated

    def _transform_ra(self, ra_deg, dec_deg):
        """
        Transform RA in degrees to RA_rot in radians
        where RA was from a catalog centered on
        RA=0, Dec=0 and RA_rot is from a catalog
        centered on RA=self.field_ra, Dec=self.field_dec
        """
        ra, dec = self._rotate_to_correct_field(deg2rad_double(ra_deg),
                                                deg2rad_double(dec_deg))
        return ra

    def _transform_dec(self, ra_deg, dec_deg):
        """
        Transform Dec in degrees to Dec_rot in radians
        where Dec was from a catalog centered on
        RA=0, Dec=0 and Dec_rot is from a catalog
        centered on RA=self.field_ra, Dec=self.field_dec
        """

        ra, dec = self._rotate_to_correct_field(deg2rad_double(ra_deg),
                                                deg2rad_double(dec_deg))
        return dec

    def _transform_catalog(self, gc):
        """
        Accept a GCR catalog object and add transformations to the
        columns in order to get the quantities expected by the CatSim
        code

        Parameters
        ----------
        gc -- a GCRCatalog object;
              the result of calling GCRCatalogs.load_catalog()
        """

        gc.add_modifier_on_derived_quantities('raJ2000', self._transform_ra,
                                              'ra_true', 'dec_true')
        gc.add_modifier_on_derived_quantities('decJ2000', self._transform_dec,
                                              'ra_true', 'dec_true')

        gc.add_quantity_modifier('redshift', gc.get_quantity_modifier('redshift_true'), overwrite=True)
        gc.add_quantity_modifier('true_redshift', gc.get_quantity_modifier('redshift_true'))
        gc.add_quantity_modifier('gamma1', gc.get_quantity_modifier('shear_1'))
        gc.add_quantity_modifier('gamma2', gc.get_quantity_modifier('shear_2'))
        gc.add_quantity_modifier('kappa', gc.get_quantity_modifier('convergence'))

        gc.add_quantity_modifier('positionAngle', gc.get_quantity_modifier('position_angle_true'))

        gc.add_modifier_on_derived_quantities('majorAxis::disk', arcsec2rad, 'size_disk_true')
        gc.add_modifier_on_derived_quantities('minorAxis::disk', arcsec2rad, 'size_minor_disk_true')
        gc.add_modifier_on_derived_quantities('majorAxis::bulge', arcsec2rad, 'size_bulge_true')
        gc.add_modifier_on_derived_quantities('minorAxis::bulge', arcsec2rad, 'size_minor_bulge_true')

        gc.add_quantity_modifier('sindex::disk', gc.get_quantity_modifier('sersic_disk'))
        gc.add_quantity_modifier('sindex::bulge', gc.get_quantity_modifier('sersic_bulge'))

        return None


class bulgeDESCQAObject_protoDC2(DESCQAObject_protoDC2):
    # PhoSim uniqueIds are generated by taking
    # source catalog uniqueIds, multiplying by
    # 1024, and adding objectTypeId.  This
    # components of the same galaxy to have
    # different uniqueIds, even though they
    # share a uniqueId in the source catalog
    objectTypeId = 97

    # some column names require an additional postfix
    _postfix = '::bulge'


class diskDESCQAObject_protoDC2(DESCQAObject_protoDC2):
    objectTypeId = 107
    _postfix = '::disk'
