import os
import h5py
import numpy as np
import healpy
import multiprocessing
import time

import GCRCatalogs
from GCR import GCRQuery

from desc.sims.GCRCatSimInterface import sed_filter_names_from_catalog
from desc.sims.GCRCatSimInterface import sed_from_galacticus_mags
from lsst.sims.photUtils import BandpassDict, Sed
from lsst.sims.photUtils import cache_LSST_seds, getImsimFluxNorm
from lsst.utils import getPackageDir
from lsst.sims.utils import htmModule as htm

import argparse


def _parallel_fitting(mag_array, redshift, H0, Om0, wav_min, wav_width,
                      lsst_mag_array, out_dict, tag):

    (sed_names,
     mag_norms,
     av_arr,
     rv_arr) = sed_from_galacticus_mags(mag_array,
                                        redshift,
                                        H0, Om0,
                                        wav_min, wav_width,
                                        lsst_mag_array)

    (tot_bp_dict,
     lsst_bp_dict) = BandpassDict.loadBandpassesFromFiles()

    sed_dir = getPackageDir('sims_sed_library')
    lsst_fit_fluxes = np.zeros((6,len(sed_names)), dtype=float)
    t_start = time.time()
    ccm_w = None
    for ii in range(len(sed_names)):
        for i_bp, bp in enumerate('ugrizy'):
            m_norm = mag_norms[i_bp][ii]
            if m_norm>0.0 and not np.isfinite(m_norm):
                continue

            spec = Sed()
            spec.readSED_flambda(os.path.join(sed_dir, sed_names[ii]))
            fnorm = getImsimFluxNorm(spec, m_norm)
            try:
                assert np.isfinite(fnorm)
                assert fnorm>0.0
            except AssertionError:
                print('\n\nmagnorm %e\n\n' % (m_norm))
                raise
            spec.multiplyFluxNorm(fnorm)
            if ccm_w is None or not np.array_equal(ccm_w, spec.wavelen):
                ccm_w = np.copy(spec.wavelen)
                ax, bx = spec.setupCCMab()
            spec.addCCMDust(ax, bx, A_v=av_arr[ii], R_v=rv_arr[ii])
            assert rv_arr[ii]>0.0
            assert np.isfinite(rv_arr[ii])
            spec.redshiftSED(redshift[ii], dimming=True)
            ff = spec.calcFlux(lsst_bp_dict[bp])
            lsst_fit_fluxes[i_bp][ii] = ff
        if ii%1000==0 and ii>0:
            duration = (time.time()-t_start)/3600.0
            prediction = len(sed_names)*duration/ii
            print('%d of %d; pred %.2e left of %.2e' %
            (ii,len(sed_names), prediction-duration, prediction))

    out_dict[tag] = (sed_names, mag_norms, av_arr, rv_arr, lsst_fit_fluxes)


def do_fitting(cat, component, healpix, lim):
    """
    Fit a set of components to SEDs, Av, Rv, magNorm using sed_from_galacticus_mags

    Parameters
    ----------
    cat -- the result of GCRCatalogs.load_catalog('catalog_name')

    component -- a string; either 'disk' or 'bulge'

    healpix -- an int indicating which healpixel to fit

    lim -- an int indicating how many objects to actually fit

    Returns
    -------
    numpy arrays of:
    redshift
    galaxy_id
    sed_name
    magNorm
    Av
    Rv
    """

    filter_data = sed_filter_names_from_catalog(cat)
    filter_names = filter_data[component]['filter_name']
    lsst_filter_names = filter_data[component]['lsst_fluxes']
    wav_min = filter_data[component]['wav_min']
    wav_width = filter_data[component]['wav_width']

    H0 = cat.cosmology.H0.value
    Om0 = cat.cosmology.Om0

    healpix_query = GCRQuery('healpix_pixel==%d' % healpix)

    qties = cat.get_quantities(list(filter_names) + list(lsst_filter_names) +
                              ['redshift_true', 'galaxy_id'],
                               native_filters=[healpix_query])

    print("testing on %d of %d" % (lim, len(qties['galaxy_id'])))
    with np.errstate(divide='ignore', invalid='ignore'):
        mag_array = np.array([-2.5*np.log10(qties[ff][:lim])
                              for ff in filter_names])

        lsst_mag_array = np.array([-2.5*np.log10(qties[ff][:lim])
                                   for ff in lsst_filter_names])


    redshift = qties['redshift_true'][:lim]
    (sed_names,
     mag_norms,
     av_arr,
     rv_arr) = sed_from_galacticus_mags(mag_array[:,:2],
                                        redshift[:2],
                                        H0, Om0,
                                        wav_min, wav_width,
                                        lsst_mag_array[:,:2])

    mgr = multiprocessing.Manager()
    out_dict = mgr.dict()
    d_gal = len(redshift)//20
    p_list = []
    for i_start in range(0, len(redshift), d_gal):
        s = slice(i_start, i_start+d_gal)
        p = multiprocessing.Process(target=_parallel_fitting,
                                    args=(mag_array[:,s], redshift[s],
                                          H0, Om0, wav_min, wav_width,
                                          lsst_mag_array[:,s],
                                          out_dict, i_start))
        p.start()
        p_list.append(p)

    for p in p_list:
        p.join()

    sed_names = np.empty(len(redshift), dtype=(str,200))
    mag_norms = np.zeros((6,len(redshift)), dtype=float)
    av_arr = np.zeros(len(redshift), dtype=float)
    rv_arr = np.zeros(len(redshift), dtype=float)
    lsst_fluxes = np.zeros((6,len(redshift)), dtype=float)

    for i_start in out_dict.keys():
        s = slice(i_start, i_start+d_gal)
        sed_names[s] = out_dict[i_start][0]
        mag_norms[:,s] = out_dict[i_start][1]
        av_arr[s] = out_dict[i_start][2]
        rv_arr[s] = out_dict[i_start][3]
        lsst_fluxes[:,s] = out_dict[i_start][4]

    return (redshift, qties['galaxy_id'][:lim],
            sed_names, mag_norms, av_arr, rv_arr, lsst_fluxes)


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument('--healpix', type=int, default=None,
                        help='The healpixel to fit')
    parser.add_argument('--out_dir', type=str, default=None,
                        help='The directory in which to write the output file')
    parser.add_argument('--lim', type=int, default=None,
                        help='The number of galaxies to fit (if you are just testing)')
    parser.add_argument('--out_name', type=str, default=None,
                        help='The name of the output file')
    args = parser.parse_args()
    assert args.healpix is not None
    assert args.out_dir is not None
    assert args.out_name is not None
    if not os.path.isdir(args.out_dir):
        os.makedirs(args.out_dir)

    sed_dir = getPackageDir('sims_sed_library')

    cat = GCRCatalogs.load_catalog('cosmoDC2_v1.0_image')
    h_query = GCRQuery('healpix_pixel==%d' % args.healpix)
    if args.lim is None:
        gid = cat.get_quantities('galaxy_id', native_filters=[h_query])['galaxy_id']
        args.lim = 2*len(gid)

    out_file_name = os.path.join(args.out_dir,args.out_name)

    ########## actually fit SED, magNorm, and dust parameters to disks and bulges

    (disk_redshift, disk_id, disk_sed_name, disk_magnorm,
     disk_av, disk_rv, disk_lsst_fluxes) = do_fitting(cat, 'disk',
                                                      args.healpix, args.lim)

    print("fit disks")

    (bulge_redshift, bulge_id, bulge_sed_name, bulge_magnorm,
     bulge_av, bulge_rv, bulge_lsst_fluxes) = do_fitting(cat, 'bulge',
                                                         args.healpix, args.lim)

    print("fit bulges")

    np.testing.assert_array_equal(disk_id, bulge_id)
    np.testing.assert_array_equal(disk_redshift, bulge_redshift)

    tot_lsst_fluxes = bulge_lsst_fluxes + disk_lsst_fluxes

    ############ get true values of magnitudes from extragalactic catalog;
    ############ adjust magNorm to demand agreement

    q_list = ['galaxy_id', 'ra_true', 'dec_true']
    for bp in 'ugrizy':
        q_list.append('mag_true_%s_lsst' % bp)

    control_qties = cat.get_quantities(q_list, native_filters=[h_query])
    for kk in control_qties:
        control_qties[kk] = control_qties[kk][:args.lim]

    print("got controls")

    np.testing.assert_array_equal(control_qties['galaxy_id'], disk_id)

    dummy_spec = Sed()
    for i_bp, bp in enumerate('ugrizy'):
        cosmodc2_flux = dummy_spec.fluxFromMag(control_qties['mag_true_%s_lsst' % bp])
        flux_ratio = cosmodc2_flux/tot_lsst_fluxes[i_bp]
        dmag = -2.5*np.log10(flux_ratio)
        disk_magnorm[i_bp,:] += dmag
        bulge_magnorm[i_bp,:] += dmag

    ############# save everything in an hdf5 file

    htmid_6 = htm.findHtmid(control_qties['ra_true'],
                            control_qties['dec_true'],
                            max_level=6)

    with h5py.File(out_file_name, 'w') as out_file:
        out_file.create_dataset('galaxy_id', data=control_qties['galaxy_id'])
        out_file.create_dataset('htmid_6', data=htmid_6)
        out_file.create_dataset('redshift', data=disk_redshift)
        out_file.create_dataset('disk_sed', data=[s.encode('utf-8') for s in disk_sed_name])
        out_file.create_dataset('bulge_sed', data=[s.encode('utf-8') for s in bulge_sed_name])
        out_file.create_dataset('bulge_magnorm', data=bulge_magnorm)
        out_file.create_dataset('disk_magnorm', data=disk_magnorm)
        out_file.create_dataset('disk_av', data=disk_av)
        out_file.create_dataset('disk_rv', data=disk_rv)
        out_file.create_dataset('bulge_av', data=bulge_av)
        out_file.create_dataset('bulge_rv', data=bulge_rv)
        for bp in 'ugrizy':
            out_file.create_dataset('cosmo_%s' % bp,
                           data=control_qties['mag_true_%s_lsst' % bp])
