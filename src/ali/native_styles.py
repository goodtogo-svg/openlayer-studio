"""Native material bevel used on a separately parameterized frame material plate."""
import math


def validate_bevel(v):
    if not isinstance(v,dict):raise ValueError('Bevel parameters must be an object')
    limits={'size_px':(0,40),'soften_px':(0,20),'depth_percent':(0,1000),'light_angle':(-360,360),'altitude':(0,90),'highlight_opacity':(0,100),'shadow_opacity':(0,100)}
    if set(v)!=set(limits)|{'highlight_rgb','shadow_rgb'}:raise ValueError('Incomplete or unsupported bevel settings')
    for key,(lo,hi) in limits.items():
        x=v[key]
        if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or not lo<=x<=hi:raise ValueError('Invalid bevel setting')
    for key in ('highlight_rgb','shadow_rgb'):
        if len(v[key])!=3 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or not 0<=x<=255 for x in v[key]):raise ValueError('Invalid bevel color')


PHOTOSHOP_BEVEL_JS=r'''
function nativeBevel(layer,v){
 doc.activeLayer=layer;var c=charIDToTypeID,s=stringIDToTypeID,fx=new ActionDescriptor(),b=new ActionDescriptor();fx.putUnitDouble(c('Scl '),c('#Prc'),100);
 b.putEnumerated(s('bevelStyle'),s('bevelEmbossStyle'),s('innerBevel'));b.putEnumerated(s('bevelTechnique'),s('bevelTechnique'),s('softMatte'));b.putEnumerated(s('bevelDirection'),s('bevelEmbossStampStyle'),s('out'));
 b.putBoolean(s('enabled'),true);b.putBoolean(s('present'),true);b.putBoolean(s('showInDialog'),true);b.putBoolean(s('useGlobalAngle'),false);
 b.putUnitDouble(s('localLightingAngle'),c('#Ang'),v.light_angle);b.putUnitDouble(s('localLightingAltitude'),c('#Ang'),v.altitude);b.putUnitDouble(s('strengthRatio'),c('#Prc'),v.depth_percent);b.putUnitDouble(s('blur'),c('#Pxl'),v.size_px);b.putUnitDouble(s('softness'),c('#Pxl'),v.soften_px);
 b.putEnumerated(s('highlightMode'),c('BlnM'),c('Scrn'));b.putEnumerated(s('shadowMode'),c('BlnM'),c('Mltp'));b.putUnitDouble(s('highlightOpacity'),c('#Prc'),v.highlight_opacity);b.putUnitDouble(s('shadowOpacity'),c('#Prc'),v.shadow_opacity);
 var cols=[v.highlight_rgb,v.shadow_rgb],keys=['highlightColor','shadowColor'];for(var i=0;i<2;i++){var col=new ActionDescriptor();col.putDouble(c('Rd  '),cols[i][0]);col.putDouble(c('Grn '),cols[i][1]);col.putDouble(c('Bl  '),cols[i][2]);b.putObject(s(keys[i]),c('RGBC'),col);}b.putBoolean(s('useTexture'),false);fx.putObject(s('bevelEmboss'),s('bevelEmboss'),b);
 var d=new ActionDescriptor(),r=new ActionReference();r.putProperty(c('Prpr'),c('Lefx'));r.putEnumerated(c('Lyr '),c('Ordn'),c('Trgt'));d.putReference(c('null'),r);d.putObject(c('T   '),c('Lefx'),fx);executeAction(c('setd'),d,DialogModes.NO);
}
'''
