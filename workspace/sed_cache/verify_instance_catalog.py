import os
import numpy as np
import pandas as pd
import healpy

import GCRCatalogs
from GCR import GCRQuery

from lsst.sims.utils import angularSeparation
from lsst.sims.photUtils import BandpassDict, Sed, Bandpass

def get_sed(name, magnorm, redshift, av, rv):
    if not hasattr(get_sed, '_rest_dict'):
        get_sed._rest_dict = {}
        get_sed._imsim_bp = Bandpass()
        get_sed._imsim_bp.imsimBandpass()
        get_sed._sed_dir = os.environ['SIMS_SED_LIBRARY_DIR']
        get_sed._ccm_w = None

    tag = '%s_%.2f_%.2f' % (name, av, rv)
    if tag not in get_sed._rest_dict:
        ss = Sed()
        ss.readSED_flambda(os.path.join(get_sed._sed_dir, name))
        if get_sed._ccm_w is None or not np.array_equal(ss.wavelen, get_sed._ccm_w):
            get_sed._ccm_w = np.copy(ss.wavelen)
            get_sed._ax, get_sed._bx = ss.setupCCM_ab()

        mn = ss.calcMag(get_sed._imsim_bp)
        ss.addDust(get_sed._ax, get_sed._bx, A_v=av, R_v=rv)
        get_sed._rest_dict[tag] = (ss, mn)

    base_sed = get_sed._rest_dict[tag][0]
    ss = Sed(wavelen = base_sed.wavelen, flambda=base_sed.flambda)
    dmag = magnorm-get_sed._rest_dict[tag][1]
    fnorm = np.power(10.0,-0.4*dmag)
    ss.multiplyFluxNorm(fnorm)
    ss.redshiftSED(redshift, dimming=True)
    return ss

colnames = ['obj', 'uniqueID', 'ra', 'dec', 'magnorm', 'sed', 'redshift', 'g1', 'g2',
            'kappa', 'dra', 'ddec', 'src_type', 'major', 'minor',
            'positionAngle', 'sindex', 'dust_rest', 'rest_av', 'rest_rv',
            'dust_obs', 'obs_av', 'obs_rv']

col_types = {'magnorm': float, 'redshift': float,
             'rest_av': float, 'rest_rv': float,
             'sed': bytes, 'uniqueID': int}

data_dir = os.path.join(os.environ['SCRATCH'], 'instcat_181024_verify', '00277065')
assert os.path.isdir(data_dir)

phosim_file = os.path.join(data_dir, 'phosim_cat_277065.txt')
assert os.path.isfile(phosim_file)
bandpass_name = None
bandpass_name_list = 'ugrizy'
with open(phosim_file, 'r') as in_file:
    for line in in_file:
        params = line.strip().split()
        if params[0] == 'filter':
            bandpass_name = bandpass_name_list[int(params[1])]

assert bandpass_name is not None

(tot_dict,
 hw_dict) = BandpassDict.loadBandpassesFromFiles()

bandpass = hw_dict[bandpass_name]
nrows = 10000

disk_file = os.path.join(data_dir, 'disk_gal_cat_277065.txt.gz')
assert os.path.isfile(disk_file)

bulge_file = os.path.join(data_dir, 'bulge_gal_cat_277065.txt.gz')
assert os.path.isfile(bulge_file)

knots_file = os.path.join(data_dir, 'knots_cat_277065.txt.gz')
assert os.path.isfile(knots_file)

disk_df = pd.read_csv(disk_file, delimiter=' ',
                        compression='gzip', names=colnames, dtype=col_types, nrows=None)

disk_df['galaxy_id'] = pd.Series(disk_df['uniqueID']//1024, index=disk_df.index)
disk_df = disk_df.set_index('galaxy_id')

bulge_df = pd.read_csv(bulge_file, delimiter=' ',
                       compression='gzip', names=colnames, dtype=col_types, nrows=None)
bulge_df['galaxy_id'] = pd.Series(bulge_df['uniqueID']//1024, index=bulge_df.index)
bulge_df = bulge_df.set_index('galaxy_id')

for ii in range(len(colnames)):
    colnames[ii] = colnames[ii]+'_knots'

knots_df = pd.read_csv(knots_file, delimiter=' ',
                       compression='gzip', names=colnames, dtype=col_types, nrows=None)
knots_df['galaxy_id'] = pd.Series(knots_df['uniqueID_knots']//1024, index=knots_df.index)
knots_df = knots_df.set_index('galaxy_id')

wanted_col = ['sed', 'magnorm', 'redshift', 'rest_av', 'rest_rv', 'ra', 'dec']
galaxy_df = disk_df[wanted_col].join(bulge_df[wanted_col], how='outer', lsuffix='_disk', rsuffix='_bulge')
for ii in range(len(wanted_col)):
    wanted_col[ii] = wanted_col[ii]+'_knots'
galaxy_df = galaxy_df.join(knots_df[wanted_col], how='outer', rsuffix='_knots')

rng = np.random.RandomState(9999)
dexes = rng.choice(galaxy_df.index.values, size=nrows, replace=False)

galaxy_df = galaxy_df.loc[dexes]

galaxy_df = galaxy_df.sort_index()

ra_center = np.nanmedian(galaxy_df['ra_disk'].values)
dec_center = np.nanmedian(galaxy_df['dec_disk'].values)

dd = angularSeparation(ra_center, dec_center, galaxy_df['ra_disk'].values, galaxy_df['dec_disk'].values)
radius_deg = np.nanmax(dd)
ra_rad = np.radians(ra_center)
dec_rad = np.radians(dec_center)
vv = np.array([np.cos(ra_rad)*np.cos(dec_rad),
               np.sin(ra_rad)*np.cos(dec_rad),
               np.sin(dec_rad)])

healpix_list = healpy.query_disc(32, vv, np.radians(radius_deg),
                                 nest=False, inclusive=True)

print('healpix list')
print(healpix_list)
print(ra_center, dec_center)

hp_query = GCRQuery()
for hp in healpix_list:
    hp_query |= GCRQuery('healpix_pixel==%d' % hp)

print('len(galaxy_df) ',len(galaxy_df))
print('built final df')
cat = GCRCatalogs.load_catalog('cosmoDC2_v1.0_image')
gid = cat.get_quantities('galaxy_id', native_filters=[hp_query])['galaxy_id']
print('loaded galaxy_id')
valid_dexes = np.where(np.in1d(gid, galaxy_df.index.values,assume_unique=True))
print('got valid_dexes')
print(len(valid_dexes[0]))
print(len(galaxy_df))

sorted_dex = np.argsort(gid[valid_dexes])
valid_dexes = valid_dexes[0][sorted_dex]
gid = gid[valid_dexes]

np.testing.assert_array_equal(gid, galaxy_df.index.values)

mag_name = 'mag_true_%s_lsst' % bandpass_name
mags = cat.get_quantities(mag_name, native_filters=[hp_query])[mag_name][valid_dexes]

d_mag_max = -1.0

for g, mag_true, (index, row) in zip(gid, mags, galaxy_df.iterrows()):
    assert g==index

    if np.isnan(row['magnorm_disk']):
        disk_flux = 0.0
    else:
        ss = get_sed(row['sed_disk'], row['magnorm_disk'],
                     row['redshift_disk'], row['rest_av_disk'],
                     row['rest_rv_disk'])
        disk_mag = ss.calcMag(bandpass)
        disk_flux = np.power(10.0,-0.4*disk_mag)

    if np.isnan(row['magnorm_bulge']):
        bulge_flux = 0.0
    else:
        ss = get_sed(row['sed_bulge'], row['magnorm_bulge'],
                     row['redshift_bulge'], row['rest_av_bulge'],
                     row['rest_rv_bulge'])
        bulge_mag = ss.calcMag(bandpass)
        bulge_flux = np.power(10.0,-0.4*bulge_mag)

    if np.isnan(row['magnorm_knots']):
        knots_flux = 0.0
    else:
        ss = get_sed(row['sed_knots'], row['magnorm_knots'],
                     row['redshift_knots'], row['rest_av_knots'],
                     row['rest_rv_knots'])
        knots_mag = ss.calcMag(bandpass)
        knots_flux = np.power(10.0,-0.4*knots_mag)

    tot_mag = -2.5*np.log10(disk_flux+bulge_flux+knots_flux)
    d_mag = np.abs(tot_mag-mag_true)
    if d_mag>d_mag_max:
        d_mag_max = d_mag
        print('d_mag_max %e -- InstanceCatalgo %e truth %e' % (d_mag_max, tot_mag, mag_true))
        #print(row)