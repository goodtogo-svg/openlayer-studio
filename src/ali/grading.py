"""Editable, clipped native Curves adjustments; not baked color filters."""
import math


def validate_curves(curves):
    if not isinstance(curves,dict) or set(curves)-{'composite','red','green','blue'}:raise ValueError('Unsupported grading channel')
    for points in curves.values():
        if not 2<=len(points)<=16 or any(len(p)!=2 for p in points):raise ValueError('Expected 2..16 curve XY points')
        if points[0][0]!=0 or points[-1][0]!=255:raise ValueError('Curve must span 0..255')
        for p in points:
            for v in p:
                if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=255:raise ValueError('Invalid grading coordinate')
        if any(b[0]<=a[0] for a,b in zip(points,points[1:])):raise ValueError('Curve X must increase')


PHOTOSHOP_GRADING_JS=r'''
function createGrade(clean,curves){
 var any=false;for(var key in curves)any=true;if(!any)return;
 doc.activeLayer=clean;var c=charIDToTypeID;
 var make=new ActionDescriptor(),ref=new ActionReference();ref.putClass(c('AdjL'));make.putReference(c('null'),ref);
 var using=new ActionDescriptor();using.putString(c('Nm  '),'FILTER - editable lighting curves');
 var type=new ActionDescriptor(),adjustments=new ActionList(),ids={composite:'Cmps',red:'Rd  ',green:'Grn ',blue:'Bl  '};
 for(var key in curves){var channel=new ActionDescriptor(),r=new ActionReference();r.putEnumerated(c('Chnl'),c('Chnl'),c(ids[key]));channel.putReference(c('Chnl'),r);
 var list=new ActionList();for(var i=0;i<curves[key].length;i++){var p=new ActionDescriptor();p.putDouble(c('Hrzn'),curves[key][i][0]);p.putDouble(c('Vrtc'),curves[key][i][1]);list.putObject(c('Pnt '),p);}channel.putList(c('Crv '),list);adjustments.putObject(c('CrvA'),channel);}
 type.putList(c('Adjs'),adjustments);using.putObject(c('Type'),c('Crvs'),type);make.putObject(c('Usng'),c('AdjL'),using);executeAction(c('Mk  '),make,DialogModes.NO);
 doc.activeLayer.grouped=true;
 if(!doc.activeLayer.grouped)throw new Error('Grading must be clipped to clean base');
}
'''
