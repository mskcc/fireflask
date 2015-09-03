$(document).ready(function() {
    $("#dbmenu a").click(function(e) {
        var base = location.protocol + '//' + location.host;
        window.location.href=base + "/" +  $(this).attr('href').substring(1);
        return false;
    });
    $("#wf_delete").click(function(e) {
        $('input[type="checkbox"]:checked').each(function() {
            console.log($(this).val());
            $.ajax({
                url: "/"+$("#dbname").text() + "/wf/" + $(this).val() + "/delete"
            }).done(function(data) { console.log(data); } )
        });
    });
});


