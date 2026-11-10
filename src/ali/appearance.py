"""Reviewed clean bases and explicit editable Photoshop appearance parameters.

This module does not claim to invert lighting or recover a hidden original.
"""
import math


def reviewed(value):
    return value.get('human_approved') is True or (value.get('review_status') == 'agent_reviewed' and bool(value.get('review_notes')))


def validate_appearance(value):
    if not isinstance(value, dict) or not reviewed(value):
        raise ValueError('Clean appearance requires explicit review and provenance')
    if not value.get('clean_image') or not value.get('support_mask') or not value.get('provenance'):
        raise ValueError('Clean image, physical support mask and provenance required')
    if value.get('status') not in ('estimated_clean', 'provided_original'):
        raise ValueError('Clean status must distinguish estimate from provided original')
    if value['status'] == 'provided_original' and not value.get('original_evidence'):
        raise ValueError('Original asset claim requires evidence')
    validate_effects(value.get('effects', {}))
    return value


def validate_effects(effects):
    if not isinstance(effects, dict) or set(effects) - {'outer_glow', 'drop_shadow'}:
        raise ValueError('Unsupported effect; currently outer_glow and drop_shadow only')
    def number(v, low, high):
        if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError('Invalid effect parameter range')
    for kind, effect in effects.items():
        allowed = {'enabled','rgb','opacity','size_px'}
        if kind == 'drop_shadow':allowed |= {'angle','distance_px'}
        if not isinstance(effect,dict) or set(effect)-allowed:
            raise ValueError('Unknown effect parameter')
        if not isinstance(effect.get('enabled'),bool):raise ValueError('Effect enabled must be boolean')
        if not isinstance(effect.get('rgb'),list) or len(effect['rgb']) != 3:raise ValueError('Expected RGB triplet')
        for v in effect['rgb']:number(v,0,255)
        number(effect.get('opacity'),0,100);number(effect.get('size_px'),0,250)
        if kind == 'drop_shadow':
            number(effect.get('angle'),-360,360);number(effect.get('distance_px'),0,30000)


# No raster filters are baked into the clean master; fx remains a native descriptor.
PHOTOSHOP_APPEARANCE_JS = r'''
function applyComponentEffects(layer,effects){
 doc.activeLayer=layer;var c=charIDToTypeID,s=stringIDToTypeID;
 var anyEnabled=false;for(var key in effects){if(effects[key].enabled)anyEnabled=true;}
 if(!anyEnabled){var check=new ActionReference();check.putEnumerated(c('Lyr '),c('Ordn'),c('Trgt'));var state=executeActionGet(check);if(state.hasKey(s('layerEffects'))&&(!state.hasKey(s('layerFXVisible'))||state.getBoolean(s('layerFXVisible')))){var hide=new ActionDescriptor(),list=new ActionList(),ref=new ActionReference();ref.putClass(s('layerEffects'));ref.putIdentifier(c('Lyr '),layer.id);list.putReference(ref);hide.putList(c('null'),list);executeAction(c('Hd  '),hide,DialogModes.NO);}return;}
 var fx=new ActionDescriptor();
 fx.putUnitDouble(c('Scl '),c('#Prc'),100);
 for(var k in effects){var e=effects[k];if(!e.enabled)continue;
  var d=new ActionDescriptor();d.putBoolean(c('enab'),true);
  d.putEnumerated(c('Md  '),c('BlnM'),k==='outer_glow'?c('Scrn'):c('Mltp'));
  d.putUnitDouble(c('Opct'),c('#Prc'),e.opacity);
  var col=new ActionDescriptor();col.putDouble(c('Rd  '),e.rgb[0]);col.putDouble(c('Grn '),e.rgb[1]);col.putDouble(c('Bl  '),e.rgb[2]);
  d.putObject(c('Clr '),c('RGBC'),col);d.putUnitDouble(c('blur'),c('#Pxl'),e.size_px);
  d.putUnitDouble(c('Ckmt'),c('#Pxl'),0);
  if(k==='drop_shadow'){d.putBoolean(c('uglg'),false);d.putUnitDouble(c('lagl'),c('#Ang'),e.angle);d.putUnitDouble(c('Dstn'),c('#Pxl'),e.distance_px);}
  var key=k==='outer_glow'?c('OrGl'):c('DrSh');fx.putObject(key,key,d);
 }
 var set=new ActionDescriptor(),ref=new ActionReference();ref.putProperty(c('Prpr'),c('Lefx'));ref.putEnumerated(c('Lyr '),c('Ordn'),c('Trgt'));
 set.putReference(c('null'),ref);set.putObject(c('T   '),c('Lefx'),fx);executeAction(c('setd'),set,DialogModes.NO);
 var check=new ActionReference();check.putEnumerated(c('Lyr '),c('Ordn'),c('Trgt'));var state=executeActionGet(check);if(state.hasKey(s('layerFXVisible'))&&!state.getBoolean(s('layerFXVisible'))){var show=new ActionDescriptor(),list=new ActionList(),ref=new ActionReference();ref.putClass(s('layerEffects'));ref.putIdentifier(c('Lyr '),layer.id);list.putReference(ref);show.putList(c('null'),list);executeAction(c('Shw '),show,DialogModes.NO);}
}
'''
