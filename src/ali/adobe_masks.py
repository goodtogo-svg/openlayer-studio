"""Native Photoshop reveal-selection masks from full-canvas grayscale assets."""
PHOTOSHOP_MASK_JS=r'''
function nativeMask(layer,file){
 var temp=app.open(new File(file)),channel=temp.channels[0].duplicate(doc);
 temp.close(SaveOptions.DONOTSAVECHANGES);app.activeDocument=doc;doc.activeLayer=layer;
 doc.activeChannels=[doc.channels[0],doc.channels[1],doc.channels[2]];
 doc.selection.load(channel,SelectionType.REPLACE);
 var c=charIDToTypeID,d=new ActionDescriptor(),ref=new ActionReference();d.putClass(c('Nw  '),c('Chnl'));ref.putEnumerated(c('Chnl'),c('Chnl'),c('Msk '));d.putReference(c('At  '),ref);d.putEnumerated(c('Usng'),c('UsrM'),c('RvlS'));executeAction(c('Mk  '),d,DialogModes.NO);
 doc.selection.deselect();channel.remove();doc.activeChannels=[doc.channels[0],doc.channels[1],doc.channels[2]];
}
'''
